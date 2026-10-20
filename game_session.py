#!/usr/bin/env python3
"""Human-vs-agent game loop over the `checkers_*` tools.

The checkers_* tool contract is documented in the README (Contract section):
the page glue's real tool list (6 tools, exact names `checkers_*`;
`play_move` takes {from, path} — NOT {from,to}).

Limitation verified in the relay source (v0.1.12/0.1.13): the two legs
(stdio bridge and daemon) cut any tools/call at **30 s**. That is why
`wait_my_turn` is requested with a 20 s timeout (<30) and the loop re-waits
on `timeout:true`; it never uses the glue's 60 s default.
"""

from __future__ import annotations

import time


class AgentError(Exception):
    pass


class HeuristicPlayer:
    """Loop opponent for tests/demos without spending LLM calls: max capture
    or first move."""

    def choose(self, board_text, moves, legal_lines):
        best = max(moves, key=lambda m: len(m.get("captured", []) or []))
        return best, "(heuristic: %s)" % legal_lines[moves.index(best)], None


class GameLogger:
    """Decision log in game.log (key-free: every line goes through scrub)."""

    def __init__(self, path, echo=True, scrub=None):
        self.path = path
        self.echo = echo
        self._scrub = scrub or (lambda s: s)

    def __call__(self, text):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), self._scrub(text))
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        if self.echo:
            print(line, flush=True)


class GameSession:
    """Agent turns. `bridge`: object with call_tool(name,args,timeout);
    `tools`: map short_name->channel-prefixed name."""

    def __init__(self, bridge, tools, picker, log):
        self.bridge = bridge
        self.tools = tools
        self.picker = picker
        self.log = log

    # --- tool helpers -------------------------------------------------------
    def _call(self, short, args=None, timeout=40):
        name = self.tools.get(short)
        if name is None:
            raise AgentError("tool not paired: %s" % short)
        return self.bridge.call_tool(name, args or {}, timeout=timeout)

    @staticmethod
    def _data(payload):
        """Handlers return {ok:true,data:{...}} or {ok:false,error,...}."""
        return payload.get("data") if isinstance(payload, dict) else None

    # --- game API -----------------------------------------------------------
    def new_game(self, difficulty=None):
        args = {"difficulty": difficulty} if difficulty else {}
        res = self._call("checkers_new_game", args)
        data = self._data(res) or {}
        if not res.get("ok"):
            raise AgentError("new_game: %s" % res.get("error", res))
        self.log("new_game ok (difficulty=%s, first to move is player %s)"
                 % (data.get("difficulty", "?"), data.get("current_player")))
        return data

    def undo(self, n=1):
        for i in range(n):
            res = self._call("checkers_undo")
            data = self._data(res) or {}
            self.log("undo %d/%d: undone=%s (removes the human+agent pair)"
                     % (i + 1, n, data.get("undone")))
        return self._call("checkers_get_state")

    def run(self, max_turns=120, wait_seconds=20, max_idle_waits=90):
        """Main loop (F6): wait_my_turn -> snapshot -> legal_moves -> picker ->
        play_move, until game over, cancellation or the move cap. Returns a
        summary dict."""
        import board as B
        played = 0
        idle_waits = 0
        while True:
            # 1) wait for the turn (push ~1 s; timeout < the relay's 30 s)
            try:
                w = self._call("checkers_wait_my_turn", {"timeout_seconds": wait_seconds})
            except Exception as e:
                raise AgentError("bridge down or tab closed: %s" % e)
            data = self._data(w) or {}
            if not w.get("ok"):
                if w.get("error") == "busy":       # >3 waits: unlikely, retry
                    time.sleep(2)
                    continue
                raise AgentError("wait_my_turn: %s" % w.get("error"))
            if data.get("cancelled"):
                self.log("the human unchecked 'Allow agent': the internal AI takes over. Ending.")
                return {"reason": "cancelled", "turns": played}
            if data.get("reason") == "game_over" or data.get("state") in ("won", "lost"):
                return self._finish(data, played)
            if data.get("timeout"):
                idle_waits += 1
                if idle_waits % 3 == 1:
                    self.log("the human is not moving (wait %d x %ds)..." % (idle_waits, wait_seconds))
                if idle_waits >= max_idle_waits:
                    raise AgentError("no human move in ~%d s: standoff, giving up."
                                     % (idle_waits * wait_seconds))
                continue
            idle_waits = 0
            if data.get("current_player") != B.AGENT_PLAYER:
                continue                            # defensive: not its turn yet

            # 2) read the legal moves (see Contract)
            lm = self._call("checkers_get_legal_moves")
            if not lm.get("ok"):
                if lm.get("error") == "not_playing":
                    st = self._data(self._call("checkers_get_state")) or {}
                    return self._finish(st, played)
                raise AgentError("get_legal_moves: %s" % lm.get("error"))
            moves = (self._data(lm) or {}).get("moves") or []
            if not moves:
                st = self._data(self._call("checkers_get_state")) or {}
                return self._finish(st, played)

            # 3) decision (LLM or heuristic) and web-validated move
            cells = (data.get("cells") or [0] * 64)
            board_text = B.render(cells)
            legal_lines = B.moves_for_prompt(moves)
            self.log("turn %d: %s\n%s" % (played + 1, " | ".join(legal_lines[:8]), board_text))
            move, raw, warn = self.picker.choose(board_text, moves, legal_lines)
            if warn:
                self.log("picker warning: %s" % warn)
            payload = B.canonical_payload(move)
            res = self._call("checkers_play_move", payload)
            d = self._data(res) or {}
            if not res.get("ok"):
                err = res.get("error")
                if err == "not_your_turn":
                    self.log("play_move: not_your_turn (race with the human): waiting again")
                    continue
                if err in ("illegal_move", "busy", "bad_args"):
                    self.log("play_move rejected (%s): recomputing" % err)
                    continue
                raise AgentError("play_move: %s" % err)
            played += 1
            self.log("move %d: %s -> %s captures=%s (state %s, winner %s) [%s]"
                     % (played, payload["from"], payload["path"],
                        d.get("captured"), d.get("state"), d.get("winner"),
                        (raw[:80] if isinstance(raw, str) else raw)))
            if d.get("winner") or d.get("state") in ("won", "lost"):
                snap = self._data(self._call("checkers_get_state")) or d
                return self._finish(snap, played)
            if played >= max_turns:
                raise AgentError("agent move limit reached (%d)" % max_turns)

    def _finish(self, snap, played):
        winner = snap.get("winner", 0)
        state = snap.get("state", "?")
        txt = ("GAME OVER: state=%s winner=%s (%s) after %d agent moves"
               % (state, winner,
                  {1: "the human wins", 2: "the agent wins"}.get(winner, "no winner"),
                  played))
        self.log(txt)
        return {"reason": "game_over", "winner": winner, "state": state, "turns": played}
