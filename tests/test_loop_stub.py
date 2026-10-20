#!/usr/bin/env python3
"""Full-loop test against the bridge STUB (no browser, no network).

It demonstrates (b) of the assignment: a real MCP handshake (ndjson) ->
detection of the 6 paired tools -> new_game -> wait_my_turn ->
get_legal_moves -> picker -> play_move -> ... -> game over, with the exact
result protocol of the glue ({ok,data}/{ok,error}, default channel prefix
`tabandblank_com-` — the production format).
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import mcp_stdio        # noqa: E402
import board            # noqa: E402
from game_session import GameSession, HeuristicPlayer  # noqa: E402

STUB = [sys.executable, os.path.join(HERE, "..", "stub_bridge.py")]


class QuietLog:
    def __init__(self):
        self.lines = []

    def __call__(self, text):
        self.lines.append(str(text))


class TestStubLoop(unittest.TestCase):
    def setUp(self):
        self.bridge = mcp_stdio.McpBridge(STUB)
        self.bridge.start(timeout=30)
        self.tools = self.bridge.find_checkers_tools()

    def tearDown(self):
        self.bridge.stop()

    def test_handshake_and_listing(self):
        self.assertEqual(len(self.tools), 6)
        for short, full in self.tools.items():
            # default stub channel = the production prefix (tabandblank_com-)
            self.assertTrue(full.startswith("tabandblank_com-"), full)

    def test_token_via_tool(self):
        tok = self.bridge.get_pairing_token()
        data = json.loads(__import__("base64").b64decode(tok))
        self.assertEqual(data["server"], "ws://localhost:4797")
        self.assertEqual(len(data["token"]), 32)

    def test_scripted_game_3_cycles(self):
        log = QuietLog()
        session = GameSession(self.bridge, self.tools, HeuristicPlayer(), log)
        session.new_game("medium")
        summary = session.run(max_turns=10, wait_seconds=1)
        self.assertEqual(summary["reason"], "game_over")
        self.assertEqual(summary["winner"], 2)       # the agent wins (state "lost" for P1)
        self.assertEqual(summary["turns"], 3)
        joined = "\n".join(log.lines)
        self.assertIn("GAME OVER:", joined)

    def test_play_move_validated_against_stub_list(self):
        res = self.bridge.call_tool(self.tools["checkers_new_game"], {})
        self.assertTrue(res["ok"])
        bad = self.bridge.call_tool(self.tools["checkers_play_move"],
                                    {"from": 99, "path": [8]})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "illegal_move")
        good = self.bridge.call_tool(self.tools["checkers_play_move"],
                                     {"from": 39, "path": [32]})
        self.assertTrue(good["ok"])
        st = self.bridge.call_tool(self.tools["checkers_get_state"], {})
        self.assertEqual(len(st["data"]["cells"]), 64)
        # after the agent's move the script moves the human: cp can be 1 or 2
        self.assertIn(st["data"]["current_player"], (1, 2))

    def test_wait_immediate_when_already_turn(self):
        self.bridge.call_tool(self.tools["checkers_new_game"], {})
        w = self.bridge.call_tool(self.tools["checkers_wait_my_turn"],
                                  {"timeout_seconds": 1})
        self.assertTrue(w["ok"])
        self.assertTrue(w["data"].get("immediate"))
        self.assertEqual(w["data"]["current_player"], board.AGENT_PLAYER)


class TestPickerFallback(unittest.TestCase):
    """A player answering garbage cannot break the loop: GameSession delegates
    legality to the picker and to the page; here the
    play_move->error->re-loop path is exercised with a picker that picks a
    non-existent move TWICE and then hits (the stub answers illegal_move first)."""

    class FlakyPicker:
        def __init__(self, moves_pool):
            self.n = 0
            self.pool = moves_pool

        def choose(self, board_text, moves, legal_lines):
            self.n += 1
            if self.n == 1:
                # a move that is NOT in `moves`: the page will reject it
                fake = {"from": 63, "path": [0], "captured": []}
                return fake, "tried an illegal move", None
            return moves[0], "ok", None

    def test_reloop_after_illegal(self):
        bridge = mcp_stdio.McpBridge(STUB)
        bridge.start(timeout=30)
        try:
            tools = bridge.find_checkers_tools()
            log = QuietLog()
            session = GameSession(bridge, tools, self.FlakyPicker(None), log)
            session.new_game()
            summary = session.run(max_turns=10, wait_seconds=1)
            self.assertEqual(summary["turns"], 3)
            self.assertIn("illegal_move", "\n".join(log.lines))
        finally:
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
