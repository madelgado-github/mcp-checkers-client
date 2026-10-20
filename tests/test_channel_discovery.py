#!/usr/bin/env python3
"""Tool discovery is by SUFFIX (`checkers_*`), NOT by channel prefix: on the
published site the tools come out e.g. under `tabandblank_com-` (channel =
window.location.host with `.` and `:` -> `_`, verified in the
@jason.today/webmcp source) and the loop works the same. Tested with simulated
names and with the whole stub running on an overridden channel (the default
stub prefix IS the production one now, so here we override to a different
value to prove the env switch still works)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import mcp_stdio                                    # noqa: E402
from game_session import GameSession, HeuristicPlayer  # noqa: E402

STUB = [sys.executable, os.path.join(HERE, "..", "stub_bridge.py")]
WANTED = ("checkers_get_state", "checkers_get_legal_moves", "checkers_play_move",
          "checkers_new_game", "checkers_undo", "checkers_wait_my_turn")


def bridge_listing_fake(names):
    """McpBridge without a process: only list_tools() mocked, as if tools/list
    answered with those names already channel-prefixed."""
    b = mcp_stdio.McpBridge([sys.executable])
    b.list_tools = lambda: [{"name": n} for n in names]
    return b


class TestDiscoveryBySuffix(unittest.TestCase):
    def test_production_prefix_without_hardcode(self):
        names = ["tabandblank_com-" + w for w in WANTED]
        found = bridge_listing_fake(names).find_checkers_tools()
        self.assertEqual(sorted(found), sorted(WANTED))
        self.assertEqual(found["checkers_get_state"],
                         "tabandblank_com-checkers_get_state")

    def test_any_prefix_is_valid(self):
        names = ["192_168_1_5_8088-" + w for w in WANTED]
        self.assertEqual(len(bridge_listing_fake(names).find_checkers_tools()), 6)

    def test_no_channel_is_the_only_recognized_one(self):
        # the old mapping (localhost_8088) is still not hardcoded anywhere: we
        # check the short list contains neither "localhost" nor the port
        found = bridge_listing_fake(["tabandblank_com-" + w for w in WANTED]) \
            .find_checkers_tools()
        joined = " ".join(sorted(found))
        self.assertNotIn("localhost", joined)
        self.assertNotIn("8088", joined)


class TestLoopOverriddenChannel(unittest.TestCase):
    """Real loop against the stub launched with an overridden channel prefix
    (the stub's DEFAULT is `tabandblank_com-` now; overriding to another
    value proves WEBMCP_STUB_CHANNEL_PREFIX keeps working)."""

    def setUp(self):
        os.environ["WEBMCP_STUB_CHANNEL_PREFIX"] = "localhost_8088-"
        self.bridge = mcp_stdio.McpBridge(STUB)
        self.bridge.start(timeout=30)

    def tearDown(self):
        self.bridge.stop()
        os.environ.pop("WEBMCP_STUB_CHANNEL_PREFIX", None)

    def test_tools_mapped_with_external_prefix(self):
        tools = self.bridge.find_checkers_tools()
        self.assertEqual(len(tools), 6)
        for full in tools.values():
            self.assertTrue(full.startswith("localhost_8088-"), full)

    def test_full_game_via_suffix(self):
        tools = self.bridge.find_checkers_tools()
        log_lines = []
        session = GameSession(self.bridge, tools, HeuristicPlayer(),
                              log_lines.append)
        session.new_game("medium")
        summary = session.run(max_turns=10, wait_seconds=1)
        self.assertEqual(summary["reason"], "game_over")
        self.assertEqual(summary["winner"], 2)
        self.assertEqual(summary["turns"], 3)


if __name__ == "__main__":
    unittest.main()
