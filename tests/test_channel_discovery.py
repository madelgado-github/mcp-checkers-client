#!/usr/bin/env python3
"""El descubrimiento de tools es por SUFIJO (`checkers_*`), NO por prefijo de
canal: con el sitio publicado las tools salen p. ej. bajo `tabandblank_com-`
(canal = window.location.host con `.` y `:` → `_`, verificado en la fuente de
@jason.today/webmcp) y el bucle funciona igual. Se prueba con nombres
simulados y con el stub entero arrancado en "canal producción"."""

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
    """McpBridge sin proceso: solo list_tools() mockeado, como si tools/list
    respondiera con esos nombres ya prefijados por el canal."""
    b = mcp_stdio.McpBridge([sys.executable])
    b.list_tools = lambda: [{"name": n} for n in names]
    return b


class TestDiscoveryPorSufijo(unittest.TestCase):
    def test_prefijo_produccion_sin_hardcode(self):
        names = ["tabandblank_com-" + w for w in WANTED]
        found = bridge_listing_fake(names).find_checkers_tools()
        self.assertEqual(sorted(found), sorted(WANTED))
        self.assertEqual(found["checkers_get_state"],
                         "tabandblank_com-checkers_get_state")

    def test_cualquier_prefijo_vale(self):
        names = ["192_168_1_5_8088-" + w for w in WANTED]
        self.assertEqual(len(bridge_listing_fake(names).find_checkers_tools()), 6)

    def test_localhost_no_es_el_unico_canal_reconocido(self):
        # el mapeo antiguo (localhost_8088) sigue sin hardcodearse: se comprueba
        # que la lista corta no contiene ni "localhost" ni el puerto
        found = bridge_listing_fake(["tabandblank_com-" + w for w in WANTED]) \
            .find_checkers_tools()
        joined = " ".join(sorted(found))
        self.assertNotIn("localhost", joined)
        self.assertNotIn("8088", joined)


class TestLoopCanalProduccion(unittest.TestCase):
    """Bucle real contra el stub arrancado con canal simulado de producción."""

    def setUp(self):
        os.environ["WEBMCP_STUB_CHANNEL_PREFIX"] = "tabandblank_com-"
        self.bridge = mcp_stdio.McpBridge(STUB)
        self.bridge.start(timeout=30)

    def tearDown(self):
        self.bridge.stop()
        os.environ.pop("WEBMCP_STUB_CHANNEL_PREFIX", None)

    def test_tools_mapeadas_con_prefijo_externo(self):
        tools = self.bridge.find_checkers_tools()
        self.assertEqual(len(tools), 6)
        for full in tools.values():
            self.assertTrue(full.startswith("tabandblank_com-"), full)

    def test_partida_completa_por_sufijo(self):
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
