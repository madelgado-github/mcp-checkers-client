#!/usr/bin/env python3
"""Qwen Cloud (OpenAI-compatible) client for picking Checkers moves.

Endpoint verified live (2026-10-20):
  base_url = https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
  (the token-plan one, `alibaba-token-plan`; https://coding.dashscope.aliyuncs.com/v1
   answers 401 invalid_api_key with this key: that is the Coding plan, not tokens).

The API key arrives via `key_source.resolve_key` (order: env QWEN_API_KEY >
pairing form in memory > legacy `key_file` file, the latter already
discouraged). Every string leaving here goes through `scrub()`, which masks
the key even if some library error contains it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class QwenError(Exception):
    pass


def load_key(path) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key:
        raise QwenError("no API key in %s" % path)
    return key


SYSTEM_PROMPT = """You are player P2 (pieces x/X) in an 8x8 English-checkers \
game against a human (P1, pieces o/O). The board arrives as 64 cells with \
index i = row*8 + column (row 0 on top; the agent's pieces run toward higher \
rows). Captures are mandatory: if a chain exists, only those moves appear in \
the legal list.

You must choose EXACTLY one move from the legal-move list and answer with a \
single JSON only (no extra text), shaped:
{"from": <index>, "to": <final destination index>, "comment": "<brief>"}
For a jump chain, "to" is the last cell of the path. Do not invent moves: \
only the listed ones are valid."""


class QwenClient:
    """Minimal Chat Completions; network failures are retried with backoff.

    401/403 are raised as terminal (retrying an invalid key is pointless).
    """

    def __init__(self, base_url, model, api_key, timeout=90, retries=2,
                 enable_thinking=None, on_event=None):
        """`enable_thinking`: False/True is sent in the body; None = NOT sent
        (endpoints that do not know the flag). With False, the token-plan
        endpoint of qwen3.8-flash answers in ~2 s instead of 2-3 min
        (reasoning disabled; measured live 2026-10-20).
        `on_event`: callable(str) for loop traceability (querying model /
        elapsed time / reason of each retry); the caller pipes it into the
        logger (text already comes scrubbed from here)."""
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.enable_thinking = enable_thinking
        self.on_event = on_event

    def scrub(self, text):
        """Never let the key escape into logs/exceptions."""
        return str(text).replace(self.api_key, "***MASKED***")

    def _emit(self, text):
        if self.on_event is None:
            return
        try:
            self.on_event(self.scrub(text))
        except Exception:
            pass  # traceability never breaks a move

    def _thinking_label(self):
        if self.enable_thinking is None:
            return "no flag"
        return "on" if self.enable_thinking else "off"

    def chat(self, messages):
        url = self.base_url + "/chat/completions"
        payload = {"model": self.model, "messages": messages}
        if self.enable_thinking is not None:
            payload["enable_thinking"] = bool(self.enable_thinking)
        body = json.dumps(payload).encode("utf-8")
        self._emit("querying model %s (timeout %ds, thinking %s)..."
                   % (self.model, self.timeout, self._thinking_label()))
        t0 = time.monotonic()
        last_err = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._emit("model responded in %.1fs" % (time.monotonic() - t0))
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                masked = self.scrub("HTTP %d %s" % (e.code, detail))
                if e.code in (401, 403):
                    self._emit("aborting: authentication rejected (%s)" % masked[:160])
                    raise QwenError("Qwen rejected the authentication: %s" % masked)
                last_err = masked
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = self.scrub("%s: %s" % (type(e).__name__, e))
            if attempt < self.retries:
                wait = 1.5 * (attempt + 1)
                self._emit("attempt %d/%d failed (%s): retrying in %.1fs"
                           % (attempt + 1, self.retries + 1,
                              last_err[:160], wait))
                time.sleep(wait)
        self._emit("model silent after %d attempts (%.1fs)"
                   % (self.retries + 1, time.monotonic() - t0))
        raise QwenError("Qwen did not answer after %d attempts: %s" % (self.retries + 1, last_err))

    def build_messages(self, board_text, legal_moves, note=None, prev=None):
        """Messages for one decision. `prev`: [(previous_ask, raw)] for the
        single re-ask after an illegal move; `note`: warning when re-asking."""
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        if prev:
            for _ask, ans in prev[-1:]:
                msgs.append({"role": "assistant", "content": ans})
        content = ("BOARD (indices = y*8+x):\n%s\n\nLEGAL MOVES (from->to):\n%s\n\n%s"
                   "Answer with the JSON {\"from\":..,\"to\":..,\"comment\":..} only."
                   % (board_text, "\n".join(legal_moves),
                      (("NOTE: " + note + "\n\n") if note else "")))
        msgs.append({"role": "user", "content": content})
        return msgs


class LLMPicker:
    """Move chooser backed by an LLM client. Robustness contract:

    - raw reply illegal -> re-ask ONCE with the error note;
    - still illegal -> take the first legal move (never crashes);
    - the LLM dies (network/401) -> QwenError is propagated ONLY if there is
      no alternative; here we opt for the first legal move + a `note` warning,
      the loop does not break.
    """

    def __init__(self, client: QwenClient):
        self.client = client

    def choose(self, board_text, moves, legal_lines):
        """Returns (canonical_move, raw_reply, warning_or_None)."""
        import board as B
        prev = []
        note = None
        raw = ""
        why = None
        for _attempt in range(2):
            msgs = self.client.build_messages(board_text, legal_lines, note=note, prev=prev)
            try:
                raw = self.client.chat(msgs)
            except QwenError as e:
                why = "LLM no answer: %s" % self.client.scrub(e)  # network/auth: go fallback
                break
            parsed = B.parse_move_json(raw)
            if parsed:
                move = B.find_move(moves, parsed["from"],
                                   to_i=parsed.get("to"), path=parsed.get("path"))
                if move is not None:
                    return move, raw[:400], None
            prev.append((None, raw))
            note = ("The move proposed is not in the legal list. Pick one "
                    "exactly as listed, {\"from\":..,\"to\":..}.")
        if not moves:
            raise QwenError("no legal moves and no useful answer from the LLM")
        if why is None:
            why = "LLM proposed an illegal move twice"
        return moves[0], raw[:400], why + ": playing the first legal move"
