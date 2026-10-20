#!/usr/bin/env python3
"""Cliente MCP mínimo por stdio para el puente `@jason.today/webmcp --mcp`.

Investigado contra la fuente real (v0.1.12/0.1.13, ver README): el puente usa
`@modelcontextprotocol/sdk` con `StdioServerTransport`, cuyo framing es
**JSON-RPC 2.0 de una línea por mensaje, separadas por \\n** (ndjson), NO
Content-Length de HTTP. Verificado en vivo: initialize → tools/list →
tools/call funcionan con este cliente.

Manos libres: solo stdlib.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time


class McpError(Exception):
    """Error JSON-RPC o de transporte."""


class McpToolError(McpError):
    """tools/call devolvio isError/texto no parseable: lleva el texto crudo."""

    def __init__(self, text):
        super().__init__(text)
        self.text = text


class McpBridge:
    """Subproceso `npx -y @jason.today/webmcp@latest --mcp` hablándole por stdio.

    cmd: lista de argv para el puente (permite inyectar el stub local en tests).
    """

    def __init__(self, cmd, stderr_sink=None):
        self.cmd = list(cmd)
        self.proc = None
        self._id = 0
        self._responses = {}          # id -> queue.Queue
        self._lock = threading.Lock()
        self._stderr_sink = stderr_sink  # callable(line) opcional
        self.notifications = []      # notificaciones recibidas (p.ej. tools listChanged)
        self._protocol_version = "2024-11-05"

    # --- ciclo de vida ------------------------------------------------------
    def start(self, timeout=60):
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(target=self._read_stream, args=(self.proc.stdout,), daemon=True).start()
        threading.Thread(target=self._read_stream, args=(self.proc.stderr,), daemon=True, name="mcp-stderr").start()
        info = self.request("initialize", {
            "protocolVersion": self._protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "play_checkers.py", "version": "1.0"},
        }, timeout=timeout)
        self.notify("notifications/initialized")
        return info

    def stop(self):
        """Cierra el puente sin tocar los pipes que leen los hilos activos:
        close() concurrente de un pipe en lectura hangulea en builds
        free-threaded (3.13t, observado). El terminate/kill produce EOF y los
        hilos daemon mueren solos."""
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid),
                                    "/T", "/F"], capture_output=True, timeout=10)
                else:
                    self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    # --- transporte ---------------------------------------------------------
    def _read_stream(self, stream):
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                if stream is self.proc.stderr:
                    if self._stderr_sink:
                        self._stderr_sink(line)
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = msg.get("id")
                if mid is not None and ("result" in msg or "error" in msg):
                    with self._lock:
                        q = self._responses.pop(mid, None)
                    if q is not None:
                        q.put(msg)
                elif msg.get("method"):
                    self.notifications.append(msg)
        except (ValueError, OSError):
            return

    def _send(self, obj):
        if not self.proc or self.proc.poll() is not None:
            raise McpError("el puente MCP no está vivo")
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=40):
        with self._lock:
            self._id += 1
            mid = self._id
            q = queue.Queue()
            self._responses[mid] = q
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._responses.pop(mid, None)
            raise McpError("timeout esperando %s (%ss)" % (method, timeout))
        if "error" in resp:
            err = resp["error"]
            raise McpError("JSON-RPC %s: %s" % (err.get("code"), err.get("message")))
        return resp.get("result")

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    # --- API MCP ------------------------------------------------------------
    def list_tools(self):
        result = self.request("tools/list", {}, timeout=20)
        return result.get("tools", []) if result else []

    def call_tool(self, name, arguments=None, timeout=40):
        """tools/call y desenmallado del envoltorio MCP {content:[{text}]}.

        Devuelve el dict ya parseado del handler de la página ({ok,data}/{ok,
        error}), o {"_raw": texto} si no es JSON. Lanza McpToolError si el
        puente marca isError (incluye 'Tool call timed out' del relay a los
        30 s y 'No clients available in channel' si la pestaña se fue).
        """
        result = self.request("tools/call",
                              {"name": name, "arguments": arguments or {}},
                              timeout=timeout)
        if not isinstance(result, dict):
            raise McpToolError(repr(result))
        if result.get("isError"):
            raise McpToolError(_content_text(result))
        text = _content_text(result)
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {"_raw": text}

    # --- helpers específicos del flujo webmcp -------------------------------
    def get_pairing_token(self) -> str:
        """Token de emparejamiento: la tool local del puente `_webmcp_get-token`
        (verificado: genera crypto-random 16B, lo escribe en
        ~/.webmcp/.webmcp-tokens.json clave '/localhost_4797' y devuelve el
        base64 de {"server":"ws://localhost:4797","token":...})."""
        result = self.request("tools/call", {"name": "_webmcp_get-token", "arguments": {}})
        text = _content_text(result)
        for line in reversed([l for l in text.splitlines() if l.strip()]):
            return line.strip()
        raise McpToolError("token vacío: %r" % text)

    def find_checkers_tools(self, wanted=("checkers_get_state", "checkers_get_legal_moves",
                                          "checkers_play_move", "checkers_new_game",
                                          "checkers_undo", "checkers_wait_my_turn")):
        """tools/list → mapa corto→full de las checkers_* emparejadas.

        El daemon prefija con el canal: `localhost_8088-checkers_get_state`
        (formato verificado en fuente y en vivo). Devuelve {} si aún no hay
        pestaña emparejada.
        """
        out = {}
        for t in self.list_tools():
            name = t.get("name", "")
            for w in wanted:
                if name == w or name.endswith("-" + w):
                    out[w] = name
        return out

    def wait_paired(self, timeout=300, poll=2.0, log=print):
        """Bloquea hasta que las 6 tools del juego aparezcan (pestaña emparejada)."""
        end = time.time() + timeout
        while time.time() < end:
            found = self.find_checkers_tools()
            if len(found) == 6:
                return found
            time.sleep(poll)
        raise McpError("no se emparejó ninguna pestaña en %ds (tools vistas: %d/6)"
                       % (timeout, len(self.find_checkers_tools())))


def _content_text(result):
    for part in result.get("content", []) or []:
        if part.get("type") == "text":
            return part.get("text", "")
    return ""


def npx_webmcp(flag: str):
    """argv canónico del puente, el mismo que usa opencode."""
    exe = "npx.cmd" if sys.platform == "win32" else "npx"
    return [exe, "-y", "@jason.today/webmcp@latest", flag]
