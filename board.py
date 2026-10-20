#!/usr/bin/env python3
"""Checkers board for the WebMCP agent: legible render + move utilities.

The checkers_* tool contract is documented in the README (Contract section).
`cells` is an array of 64 ints, index `y*8+x` (row y=0 on top), codes:
  0 = empty, 1 = P1 piece (human), 2 = P2 piece (agent),
  17 = P1 king, 18 = P2 king.

Moves: {from:int, path:[int], captured:[int]}; `path` = the FULL sequence of
destination cells (includes EVERY jump of a chain).

Pure stdlib, no dependencies.
"""

from __future__ import annotations

AGENT_PLAYER = 2
HUMAN_PLAYER = 1

EMPTY, P1, P2, KING1, KING2 = 0, 1, 2, 17, 18

_GLYPH = {EMPTY: ".", P1: "o", P2: "x", KING1: "O", KING2: "X"}


def is_king(code: int) -> bool:
    return code in (KING1, KING2)


def owner(code: int) -> int:
    """0/1/2 depending on who occupies the cell (kings included)."""
    if code in (P1, KING1):
        return 1
    if code in (P2, KING2):
        return 2
    return 0


def cell_name(idx: int) -> str:
    """'r3 c5' = row 3 (0 on top), column 5 (0 on the left)."""
    return "r%d c%d" % (idx // 8, idx % 8)


def render(cells) -> str:
    """ASCII render of the 8x8 board with row/column axes, prompt-ready."""
    if len(cells) != 64:
        raise ValueError("cells must have 64 ints, has %d" % len(cells))
    head = "     " + " ".join(str(c) for c in range(8))
    lines = [head]
    for y in range(8):
        row = " ".join(_GLYPH[int(cells[y * 8 + x])] for x in range(8))
        lines.append("  y%d  %s" % (y, row))
    lines.append("  (legend: o/x human(P1)/agent(P2) pieces, O/X kings, . empty; index = y*8+x)")
    return "\n".join(lines)


def moves_for_prompt(moves) -> list:
    """Legible descriptions: '21->17 (1 capture)' with coordinates."""
    out = []
    for m in moves:
        frm = int(m["from"])
        path = [int(p) for p in m.get("path", [])]
        cap = len(m.get("captured", []) or [])
        dest = path[-1] if path else frm
        desc = "%d->%d (%s)" % (frm, dest, cell_name(dest))
        if len(path) > 1:
            desc += " chain " + "-".join(str(p) for p in path)
        if cap:
            desc += " [%d capture%s]" % (cap, "" if cap == 1 else "s")
        out.append(desc)
    return out


def find_move(moves, from_i, to_i=None, path=None):
    """Match the model's pick against the legal list the page provides.

    Accepts {from,to} (final destination) or {from,path}. `to` equals
    path[-1]; if several moves share a destination, the one with most captures
    wins. Returns the FULL canonical move (with its path and captured) or None.
    """
    best = None
    for m in moves:
        if int(m["from"]) != int(from_i):
            continue
        mpath = [int(p) for p in m.get("path", [])]
        if path is not None:
            cand = [int(p) for p in path]
            if cand == mpath:
                return m
            continue
        if to_i is not None and mpath and int(to_i) == mpath[-1]:
            ncap = len(m.get("captured", []) or [])
            if best is None or ncap > len(best.get("captured", []) or []):
                best = m
    return best


def canonical_payload(move) -> dict:
    """Exact body for checkers_play_move: {from, path} (see Contract)."""
    return {"from": int(move["from"]), "path": [int(p) for p in move["path"]]}


def parse_move_json(text: str):
    """Robust extraction of the move asked from the LLM.

    Accepts pure JSON, ```json fences, or JSON embedded in prose. Looks for
    the first object with ints "from" and ("to" or "path"). Returns dict
    {'from':int,'to':int?,'path':[int]?} or None if nothing useful is found.
    """
    if not text:
        return None
    import json as _json
    import re
    candidates = []
    # 1) the whole text as JSON
    candidates.append(text.strip())
    # 2) ```...``` blocks
    for block in re.findall(r"```(?:json)?\s*(.+?)```", text, flags=re.S):
        candidates.append(block.strip())
    # 3) first {..} nested-ish blob in prose
    for obj in re.findall(r"\{[^{}]*\}", text):
        candidates.append(obj)
    for cand in candidates:
        try:
            data = _json.loads(cand)
        except Exception:
            continue
        if not isinstance(data, dict) or "from" not in data:
            continue
        try:
            frm = int(data["from"])
        except (TypeError, ValueError):
            continue
        out = {"from": frm}
        if "path" in data and isinstance(data["path"], list) and data["path"]:
            try:
                out["path"] = [int(p) for p in data["path"]]
            except (TypeError, ValueError):
                continue
            return out
        if "to" in data:
            try:
                out["to"] = int(data["to"])
            except (TypeError, ValueError):
                continue
            return out
    return None
