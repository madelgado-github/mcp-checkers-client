#!/usr/bin/env python3
"""Tests de board.py (render del contrato, emparejamiento de jugadas, parseo LLM)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import board  # noqa: E402


def initial_cells():
    cells = [0] * 64
    for y in range(3):
        for x in range(8):
            if (x + y) % 2 == 1:
                cells[y * 8 + x] = 2
    for y in range(5, 8):
        for x in range(8):
            if (x + y) % 2 == 1:
                cells[y * 8 + x] = 1
    return cells


class TestRender(unittest.TestCase):
    def test_longitud_y_codigos(self):
        cells = initial_cells()
        assert len(cells) == 64
        out = board.render(cells)
        self.assertIn("x", out)   # P2 arriba
        self.assertIn("o", out)   # P1 abajo

    def test_recheca_longitud_invalida(self):
        with self.assertRaises(ValueError):
            board.render([0] * 32)   # el error histórico del boceto (32 vs 64)

    def test_damas_y_owner(self):
        assert board.owner(17) == 1 and board.owner(18) == 2
        assert board.owner(1) == 1 and board.owner(2) == 2 and board.owner(0) == 0

    def test_indice_fila_columna(self):
        assert board.cell_name(21) == "r2 c5"


class TestFindMove(unittest.TestCase):
    MOVES = [
        {"from": 21, "path": [17], "captured": [18]},
        {"from": 21, "path": [14], "captured": []},
        {"from": 30, "path": [25, 20], "captured": [26, 22]},
    ]

    def test_to_simple(self):
        m = board.find_move(self.MOVES, 21, to_i=17)
        assert m == self.MOVES[0]

    def test_path_exacto_incluye_cadena(self):
        m = board.find_move(self.MOVES, 30, path=[25, 20])
        assert m == self.MOVES[2]
        # un path parcial de cadena NO empareja (Contrato: path completo)
        assert board.find_move(self.MOVES, 30, path=[25]) is None

    def test_destino_final_de_cadena_por_to(self):
        m = board.find_move(self.MOVES, 30, to_i=20)
        assert m == self.MOVES[2]

    def test_ilegal_devuelve_none(self):
        assert board.find_move(self.MOVES, 21, to_i=99) is None
        assert board.find_move(self.MOVES, 0, to_i=1) is None

    def test_canonical_payload_solo_from_path(self):
        p = board.canonical_payload(self.MOVES[0])
        assert p == {"from": 21, "path": [17]}   # sin `captured`: ver Contrato


class TestParseMoveJson(unittest.TestCase):
    def test_json_puro(self):
        assert board.parse_move_json('{"from": 21, "to": 17, "comentario": "x"}') \
            == {"from": 21, "to": 17}

    def test_bloque_cercado(self):
        t = 'pensando...\n```json\n{"from": 5, "to": 10, "comentario": "s"}\n```'
        assert board.parse_move_json(t) == {"from": 5, "to": 10}

    def test_prosa_con_json_embutido(self):
        t = 'Juego {"from": 30, "path": [25, 20], "comentario": "doble captura"} y listo'
        assert board.parse_move_json(t) == {"from": 30, "path": [25, 20]}

    def test_garbage_devuelve_none(self):
        assert board.parse_move_json("no sé qué jugar") is None
        assert board.parse_move_json("") is None
        assert board.parse_move_json('{"to": 17}') is None   # sin from
        assert board.parse_move_json('{"from": "abc", "to": 1}') is None

    def test_enteros_como_string(self):
        # los modelos a veces emiten "21": parseo robusto lo admite via int()
        assert board.parse_move_json('{"from": "21", "to": "17"}') == {"from": 21, "to": 17}


if __name__ == "__main__":
    unittest.main()
