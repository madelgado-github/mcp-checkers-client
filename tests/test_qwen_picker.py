#!/usr/bin/env python3
"""Tests del jugador Qwen: parseo robusto, re-pregunta 1 vez, fallback primera
legal, maskara de la key. Con cliente FAKED: sin red (herméticos)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import qwen_client  # noqa: E402
from qwen_client import QwenClient, LLMPicker, QwenError  # noqa: E402

MOVES = [
    {"from": 21, "path": [17], "captured": [18]},
    {"from": 30, "path": [25], "captured": []},
]
LINES = ["21->17 [1 captura]", "30->25"]


class FakeClient(QwenClient):
    def __init__(self, replies):
        QwenClient.__init__(self, "http://invalid", "fake-model", "SECRET-KEY-123")
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if not self.replies:
            raise QwenError("agotado")
        return self.replies.pop(0)


class TestLLMPicker(unittest.TestCase):
    def test_replica_valida(self):
        p = LLMPicker(FakeClient(['{"from": 21, "to": 17, "comentario": "captura"}']))
        move, raw, warn = p.choose("tablero", MOVES, LINES)
        self.assertEqual(move, MOVES[0])
        self.assertIsNone(warn)

    def test_ilegal_repregunta_y_acierta(self):
        c = FakeClient(['{"from": 99, "to": 98}', '{"from": 30, "to": 25}'])
        p = LLMPicker(c)
        move, raw, warn = p.choose("tablero", MOVES, LINES)
        self.assertEqual(move, MOVES[1])
        self.assertEqual(c.calls, 2)
        self.assertIsNone(warn)

    def test_sigue_ilegal_toma_primera_legal_sin_crash(self):
        c = FakeClient(['{"from": 99, "to": 98}', 'basura sin json'])
        p = LLMPicker(c)
        move, raw, warn = p.choose("tablero", MOVES, LINES)
        self.assertEqual(move, MOVES[0])          # primera legal
        self.assertIn("ilegal dos veces", warn)

    def test_red_rota_fallback_sin_crash(self):
        class Dead(FakeClient):
            def chat(self, messages):
                raise QwenError("HTTP 401 Authorization header is missing")
        p = LLMPicker(Dead(['x']))
        move, raw, warn = p.choose("tablero", MOVES, LINES)
        self.assertEqual(move, MOVES[0])
        self.assertIn("sin respuesta", warn)

    def test_path_completo_tambien_aceptado(self):
        p = LLMPicker(FakeClient(['{"from": 21, "path": [17]}']))
        move, _, _ = p.choose("tablero", MOVES, LINES)
        self.assertEqual(move, MOVES[0])


class TestScrub(unittest.TestCase):
    def test_key_masked_en_error(self):
        c = QwenClient("http://x", "m", "SECRET-KEY-123")
        s = c.scrub("failed with SECRET-KEY-123 in body")
        self.assertNotIn("SECRET-KEY-123", s)
        self.assertIn("***MASKED***", s)


if __name__ == "__main__":
    unittest.main()
