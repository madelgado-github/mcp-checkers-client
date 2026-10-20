#!/usr/bin/env python3
"""Local STUB of the `@jason.today/webmcp --mcp` bridge, for loop tests.

Speaks the SAME stdio protocol verified against the real package (JSON-RPC
2.0 ndjson; initialize / tools/list / tools/call), lists the 6 `checkers_*`
tools with the production channel prefix `tabandblank_com-` (the real channel
format: window.location.host with `.` and `:` replaced by `_` — verified in
the package source; override with the `WEBMCP_STUB_CHANNEL_PREFIX` env var to
simulate any other channel) plus the 2 built-ins, and simulates ONE scripted
game of 3 human→agent cycles with fake-but-coherent moves (64-cell board
with the contract codes, {from,path,captured} moves like checkers_rules).
It does NOT reimplement the engine: it is a loop double.

Usage in tests:
    McpBridge([sys.executable, "stub_bridge.py"])  # same client as the real one
"""

from __future__ import annotations

import json
import os
import sys
import time

PREF = os.environ.get("WEBMCP_STUB_CHANNEL_PREFIX", "tabandblank_com-")

INITIAL = [0] * 64
for y in range(3):
    for x in range(8):
        if (x + y) % 2 == 1:
            INITIAL[y * 8 + x] = 2          # P2 (agent) on top
for y in range(5, 8):
    for x in range(8):
        if (x + y) % 2 == 1:
            INITIAL[y * 8 + x] = 1          # P1 (human) on the bottom

# Script: per agent turn its legal moves; after the agent's move, the
# "human" reply; at the end of the 3rd cycle the terminal one (agent wins: winner=2).
SCRIPT = [
    {"legal": [{"from": 39, "path": [32], "captured": []},
               {"from": 39, "path": [34], "captured": []},
               {"from": 42, "path": [35], "captured": []}],
     "human_move": (26, 19),
     "cells_after_human": None},
    {"legal": [{"from": 32, "path": [25], "captured": [18]},
               {"from": 39, "path": [34], "captured": []}],
     "human_move": (21, 26)},
    {"legal": [{"from": 25, "path": [18, 11], "captured": [14, 7]}],
     "human_move": (28, 21)},
]


class FakeGame:
    def __init__(self):
        self.reset()

    def reset(self):
        self.cells = list(INITIAL)
        self.cp = 1
        self.state = "playing"
        self.winner = 0
        self.turn = 0
        self.agent_moves = []

    def snapshot(self, **extra):
        d = {"cells": list(self.cells), "current_player": self.cp,
             "state": self.state, "winner": self.winner, "agent_mode": True,
             "difficulty": "medium",
             "count_p1": sum(1 for c in self.cells if c in (1, 17)),
             "count_p2": sum(1 for c in self.cells if c in (2, 18))}
        d.update(extra)
        return d

    def legal(self):
        if self.turn >= len(SCRIPT):
            return []
        return [dict(m) for m in SCRIPT[self.turn]["legal"]]

    def play(self, args):
        if self.state != "playing":
            return {"ok": False, "error": "not_playing"}
        if self.cp != 2:
            return {"ok": False, "error": "not_your_turn"}
        frm, path = args.get("from"), args.get("path")
        if not isinstance(frm, int) or not isinstance(path, list) or not path:
            return {"ok": False, "error": "bad_args"}
        for m in self.legal():
            if m["from"] == frm and m["path"] == list(path):
                self.cells[frm] = 0
                self.cells[path[-1]] = 18 if path[-1] < 8 else 2
                for cap in m["captured"]:
                    self.cells[cap] = 0
                self.agent_moves.append((frm, tuple(path)))
                # scripted "human" reply
                spec = SCRIPT[self.turn]
                self.turn += 1
                hm = spec.get("human_move")
                if hm:
                    a, b = hm
                    if self.cells[a] == 1:
                        self.cells[a] = 0
                        self.cells[b] = 1
                if self.turn >= len(SCRIPT):
                    self.state, self.winner, self.cp = "lost", 2, 1
                    return {"ok": True, "data": self.snapshot(moved=True,
                                                              captured=m["captured"],
                                                              current_player=1)}
                self.cp = 1
                return {"ok": True, "data": self.snapshot(moved=True, captured=m["captured"])}
        return {"ok": False, "error": "illegal_move"}

    def human_phase(self):
        """The stub's wait_my_turn simulates the human moving after ~0.4 s."""
        time.sleep(0.4)
        self.cp = 2


def main():
    game = FakeGame()
    out = sys.stdout

    def send(obj):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    def tool_result(payload):
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "webmcp-stub", "version": "test"}}})
        elif method == "notifications/initialized" or mid is None:
            continue
        elif method == "tools/list":
            tools = [{"name": "_webmcp_get-token", "description": "stub",
                      "inputSchema": {"type": "object", "properties": {}}},
                     {"name": "_webmcp_define-mcp-tool", "description": "stub",
                      "inputSchema": {"type": "object", "properties": {}}}]
            for short in ("checkers_get_state", "checkers_get_legal_moves",
                          "checkers_play_move", "checkers_new_game",
                          "checkers_undo", "checkers_wait_my_turn"):
                tools.append({"name": PREF + short, "description": short,
                              "inputSchema": {"type": "object", "properties": {}}})
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = (params.get("name") or "").replace(PREF, "")
            args = params.get("arguments") or {}
            if name == "_webmcp_get-token":
                fake = {"server": "ws://localhost:4797", "token": "0" * 32}
                import base64 as _b64
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{
                    "type": "text",
                    "text": "Paste this into the token input on the WebMCP website\n"
                            + _b64.b64encode(json.dumps(fake).encode()).decode()}]}})
            elif name == "checkers_new_game":
                game.reset()
                game.cp = 2
                send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                    {"ok": True, "data": {"started": True, "difficulty": "medium",
                                          "current_player": 2}})})
            elif name == "checkers_get_state":
                send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                    {"ok": True, "data": game.snapshot()})})
            elif name == "checkers_get_legal_moves":
                if game.state != "playing":
                    send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                        {"ok": False, "error": "not_playing"})})
                else:
                    send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                        {"ok": True, "data": {"moves": game.legal(),
                                              "current_player": game.cp}})})
            elif name == "checkers_play_move":
                send({"jsonrpc": "2.0", "id": mid, "result": tool_result(game.play(args))})
            elif name == "checkers_undo":
                send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                    {"ok": True, "data": game.snapshot(undone=False)})})
            elif name == "checkers_wait_my_turn":
                if game.state != "playing":
                    send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                        {"ok": True, "data": game.snapshot(reason="game_over")})})
                elif game.cp == 2:
                    send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                        {"ok": True, "data": game.snapshot(immediate=True)})})
                else:
                    game.human_phase()
                    send({"jsonrpc": "2.0", "id": mid, "result": tool_result(
                        {"ok": True, "data": game.snapshot(trigger="push")})})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": dict(
                    tool_result({"ok": False, "error": "unknown_tool"}), isError=True)})
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "unsupported method: %s" % method}})


if __name__ == "__main__":
    main()
