#!/usr/bin/env python3
"""Minimal stdio MCP client for the `@jason.today/webmcp --mcp` bridge.

Researched against the real source (v0.1.12/0.1.13, see README): the bridge
uses `@modelcontextprotocol/sdk` with `StdioServerTransport`, whose framing
is **JSON-RPC 2.0 one line per message, separated by \\n** (ndjson), NOT
HTTP Content-Length. Verified live: initialize -> tools/list -> tools/call
all work with this client.

Hands off dependencies: stdlib only.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time


class McpError(Exception):
    """JSON-RPC or transport error."""


class McpToolError(McpError):
    """tools/call returned isError/unparseable text: carries the raw text."""

    def __init__(self, text):
        super().__init__(text)
        self.text = text


class McpBridge:
    """`npx -y @jason.today/webmcp@latest --mcp` subprocess spoken to over stdio.

    cmd: argv list for the bridge (lets tests inject the local stub).
    """

    def __init__(self, cmd, stderr_sink=None):
        self.cmd = list(cmd)
        self.proc = None
        self._id = 0
        self._responses = {}          # id -> queue.Queue
        self._lock = threading.Lock()
        self._stderr_sink = stderr_sink  # optional callable(line)
        self.notifications = []      # notifications received (e.g. tools listChanged)
        self._protocol_version = "2024-11-05"

    # --- lifecycle ------------------------------------------------------------
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
        """Closes the bridge without touching the pipes the active threads
        read: a concurrent close() of a pipe mid-read hangs on free-threaded
        builds (3.13t, observed). terminate/kill produces EOF and the daemon
        threads die on their own."""
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

    # --- transport ------------------------------------------------------------
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
            raise McpError("the MCP bridge is not alive")
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
            raise McpError("timeout waiting for %s (%ss)" % (method, timeout))
        if "error" in resp:
            err = resp["error"]
            raise McpError("JSON-RPC %s: %s" % (err.get("code"), err.get("message")))
        return resp.get("result")

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    # --- MCP API --------------------------------------------------------------
    def list_tools(self):
        result = self.request("tools/list", {}, timeout=20)
        return result.get("tools", []) if result else []

    def call_tool(self, name, arguments=None, timeout=40):
        """tools/call and unwrap of the MCP {content:[{text}]} envelope.

        Returns the page handler's dict already parsed ({ok,data}/{ok,error}),
        or {"_raw": text} if it is not JSON. Raises McpToolError when the
        bridge marks isError (includes the relay's 'Tool call timed out' at
        30 s and 'No clients available in channel' if the tab went away).
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

    # --- webmcp flow helpers --------------------------------------------------
    def get_pairing_token(self) -> str:
        """Pairing token: the bridge's local tool `_webmcp_get-token`
        (verified: generates crypto-random 16B, writes it to
        ~/.webmcp/.webmcp-tokens.json under key '/localhost_4797' and returns
        the base64 of {"server":"ws://localhost:4797","token":...})."""
        result = self.request("tools/call", {"name": "_webmcp_get-token", "arguments": {}})
        text = _content_text(result)
        for line in reversed([l for l in text.splitlines() if l.strip()]):
            return line.strip()
        raise McpToolError("empty token: %r" % text)

    def find_checkers_tools(self, wanted=("checkers_get_state", "checkers_get_legal_moves",
                                          "checkers_play_move", "checkers_new_game",
                                          "checkers_undo", "checkers_wait_my_turn")):
        """tools/list -> short->full map of the paired checkers_* tools.

        The daemon prefixes with the channel: `<host>-checkers_get_state`,
        e.g. `tabandblank_com-checkers_get_state` or `localhost_8088-...`
        (format verified in the source and live). Returns {} while no tab is
        paired yet.
        """
        out = {}
        for t in self.list_tools():
            name = t.get("name", "")
            for w in wanted:
                if name == w or name.endswith("-" + w):
                    out[w] = name
        return out

    def wait_paired(self, timeout=300, poll=2.0, log=print):
        """Blocks until the 6 game tools show up (a tab is paired)."""
        end = time.time() + timeout
        while time.time() < end:
            found = self.find_checkers_tools()
            if len(found) == 6:
                return found
            time.sleep(poll)
        raise McpError("no browser tab paired in %ds (tools seen: %d/6)"
                       % (timeout, len(self.find_checkers_tools())))


def _content_text(result):
    for part in result.get("content", []) or []:
        if part.get("type") == "text":
            return part.get("text", "")
    return ""


def npx_webmcp(flag: str):
    """Canonical argv of the bridge, the same one opencode uses."""
    exe = "npx.cmd" if sys.platform == "win32" else "npx"
    return [exe, "-y", "@jason.today/webmcp@latest", flag]
