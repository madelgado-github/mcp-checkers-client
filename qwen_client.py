#!/usr/bin/env python3
"""Cliente Qwen Cloud (OpenAI-compatible) para elegir jugada de Damas.

Endpoint probado en vivo (2026-10-20):
  base_url = https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
  (el del plan de tokens `alibaba-token-plan`; https://coding.dashscope.aliyuncs.com/v1
   responde 401 invalid_api_key con esta key: es el plan Coding, no el de tokens).

La API key llega por `key_source.resolve_key` (orden: env QWEN_API_KEY >
formulario de emparejamiento en memoria > fichero legacy `key_file`, este ya
desaconsejado). Todo texto que sale de aquí pasa por `scrub()`, que la maskara
aunque un error de la librería la contenga.
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
        raise QwenError("no hay API key en %s" % path)
    return key


SYSTEM_PROMPT = """Eres el jugador P2 (fichas x/X) en una partida de Damas \
inglesas 8x8 contra un humano (P1, fichas o/O). El tablero se recibe como 64 \
casillas con índice i = fila*8 + columna (fila 0 arriba; las fichas del agente \
corren hacia filas mayores). Capturas obligatorias: si hay cadena, solo esas \
jugadas aparecen en la lista legal.

Debes elegir EXACTAMENTE una jugada de la lista de jugadas legales y responder \
SOLO un JSON (sin texto extra) con la forma:
{"from": <indice>, "to": <indice del destino final>, "comentario": "<breve>"}
Para una cadena de saltos, "to" es la última casilla del path. No inventes \
jugadas: solo las listadas son válidas."""


class QwenClient:
    """Chat Completions mínimo; los fallos de red se reintentan con backoff.

    401/403 se lanzan terminales (no sirve reintentar una key inválida).
    """

    def __init__(self, base_url, model, api_key, timeout=90, retries=2,
                 enable_thinking=None, on_event=None):
        """`enable_thinking`: False/True se manda en el body; None = NO se
        manda (endpoints que no conocen el flag). Con False, el endpoint
        token-plan de qwen3.8-flash responde en ~2 s en vez de 2-3 min
        (razonamiento desactivado; medido en vivo 2026-10-20).
        `on_event`: callable(str) para trazabilidad del bucle (consultando
        modelo / tiempo empleado / motivo de cada retry); el caller lo pasa
        al logger (el texto ya sale scrubbeado de aquí)."""
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.enable_thinking = enable_thinking
        self.on_event = on_event

    def scrub(self, text):
        """Nunca dejar escapar la key por logs/excepciones."""
        return str(text).replace(self.api_key, "***MASKED***")

    def _emit(self, text):
        if self.on_event is None:
            return
        try:
            self.on_event(self.scrub(text))
        except Exception:
            pass  # la trazabilidad nunca rompe una jugada

    def _thinking_label(self):
        if self.enable_thinking is None:
            return "sin flag"
        return "on" if self.enable_thinking else "off"

    def chat(self, messages):
        url = self.base_url + "/chat/completions"
        payload = {"model": self.model, "messages": messages}
        if self.enable_thinking is not None:
            payload["enable_thinking"] = bool(self.enable_thinking)
        body = json.dumps(payload).encode("utf-8")
        self._emit("consultando modelo %s (timeout %ds, thinking %s)..."
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
                self._emit("modelo respondió en %.1fs" % (time.monotonic() - t0))
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                masked = self.scrub("HTTP %d %s" % (e.code, detail))
                if e.code in (401, 403):
                    self._emit("aborto: autenticación rechazada (%s)" % masked[:160])
                    raise QwenError("Qwen rechazó la autenticación: %s" % masked)
                last_err = masked
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = self.scrub("%s: %s" % (type(e).__name__, e))
            if attempt < self.retries:
                wait = 1.5 * (attempt + 1)
                self._emit("intento %d/%d falló (%s): reintento en %.1fs"
                           % (attempt + 1, self.retries + 1,
                              last_err[:160], wait))
                time.sleep(wait)
        self._emit("modelo sin respuesta tras %d intentos (%.1fs)"
                   % (self.retries + 1, time.monotonic() - t0))
        raise QwenError("Qwen no respondió tras %d intentos: %s" % (self.retries + 1, last_err))

    def build_messages(self, board_text, legal_moves, note=None, prev=None):
        """messages para una decisión. `prev`: [(pregunta_anterior, cruda)] para
        el re-pregunto por jugada ilegal; `note`: aviso al re-preguntar."""
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        if prev:
            for _ask, ans in prev[-1:]:
                msgs.append({"role": "assistant", "content": ans})
        content = ("TABLERO (índices = y*8+x):\n%s\n\nJUGADAS LEGALES (from->to):\n%s\n\n%s"
                   "Responde solo el JSON {\"from\":..,\"to\":..,\"comentario\":..}."
                   % (board_text, "\n".join(legal_moves),
                      (("NOTA: " + note + "\n\n") if note else "")))
        msgs.append({"role": "user", "content": content})
        return msgs


class LLMPicker:
    """Elegidor de jugada usando un cliente LLM. Contratos de robustez:

    - responde cruda ilegal → re-pregunta UNA vez con la nota del error;
    - sigue ilegal → toma la primera legal (nunca crashes);
    - el LLM muere (red/401) → QwenError propagado SOLO si no hay alternativa;
      aquí optamos por primera legal + aviso en `note`, el loop no se rompe.
    """

    def __init__(self, client: QwenClient):
        self.client = client

    def choose(self, board_text, moves, legal_lines):
        """Devuelve (move_canonica, crudo_respuesta, aviso_o_None)."""
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
                why = "LLM sin respuesta: %s" % self.client.scrub(e)  # red/auth: a fallback
                break
            parsed = B.parse_move_json(raw)
            if parsed:
                move = B.find_move(moves, parsed["from"],
                                   to_i=parsed.get("to"), path=parsed.get("path"))
                if move is not None:
                    return move, raw[:400], None
            prev.append((None, raw))
            note = ("La jugada propuesta no está en la lista legal. Elige una "
                    "exactamente como se listó, {\"from\":..,\"to\":..}.")
        if not moves:
            raise QwenError("sin jugadas legales y sin respuesta útil del LLM")
        if why is None:
            why = "LLM propuso ilegal dos veces"
        return moves[0], raw[:400], why + ": se juega la primera legal"
