#!/usr/bin/env python3
"""Tests for board.py (contract render, move matching, LLM reply parsing)."""

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
    def test_length_and_codes(self):
        cells = initial_cells()
        assert len(cells) == 64
        out = board.render(cells)
        self.assertIn("x", out)   # P2 on top
        self.assertIn("o", out)   # P1 on the bottom

    def test_rejects_invalid_length(self):
        with self.assertRaises(ValueError):
            board.render([0] * 32)   # the historic bug of the sketch (32 vs 64)

    def test_kings_and_owner(self):
        assert board.owner(17) == 1 and board.owner(18) == 2
        assert board.owner(1) == 1 and board.owner(2) == 2 and board.owner(0) == 0

    def test_row_column_index(self):
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

    def test_exact_path_matches_chain(self):
        m = board.find_move(self.MOVES, 30, path=[25, 20])
        assert m == self.MOVES[2]
        # a partial chain path does NOT match (Contract: full path)
        assert board.find_move(self.MOVES, 30, path=[25]) is None

    def test_chain_final_destination_via_to(self):
        m = board.find_move(self.MOVES, 30, to_i=20)
        assert m == self.MOVES[2]

    def test_illegal_returns_none(self):
        assert board.find_move(self.MOVES, 21, to_i=99) is None
        assert board.find_move(self.MOVES, 0, to_i=1) is None

    def test_canonical_payload_only_from_path(self):
        p = board.canonical_payload(self.MOVES[0])
        assert p == {"from": 21, "path": [17]}   # no `captured`: see Contract


class TestParseMoveJson(unittest.TestCase):
    def test_pure_json(self):
        assert board.parse_move_json('{"from": 21, "to": 17, "comment": "x"}') \
            == {"from": 21, "to": 17}

    def test_fenced_block(self):
        t = 'thinking...\n```json\n{"from": 5, "to": 10, "comment": "s"}\n```'
        assert board.parse_move_json(t) == {"from": 5, "to": 10}

    def test_prose_with_embedded_json(self):
        t = 'I play {"from": 30, "path": [25, 20], "comment": "double capture"} and done'
        assert board.parse_move_json(t) == {"from": 30, "path": [25, 20]}

    def test_garbage_returns_none(self):
        assert board.parse_move_json("no idea what to play") is None
        assert board.parse_move_json("") is None
        assert board.parse_move_json('{"to": 17}') is None   # no from
        assert board.parse_move_json('{"from": "abc", "to": 1}') is None

    def test_ints_as_strings(self):
        # models sometimes emit "21": the robust parse admits it via int()
        assert board.parse_move_json('{"from": "21", "to": "17"}') == {"from": 21, "to": 17}


if __name__ == "__main__":
    unittest.main()
