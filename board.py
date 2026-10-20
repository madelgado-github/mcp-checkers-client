#!/usr/bin/env python3
"""Tablero de Damas para el agente WebMCP: render legible + utilidades de jugada.

El contrato de las tools checkers_* está documentado en el README (sección
Contrato). `cells` es un array de 64 enteros, índice `y*8+x` (fila y=0
arriba), códigos:
  0 = vacía, 1 = ficha P1 (humano), 2 = ficha P2 (agente),
  17 = dama P1, 18 = dama P2.

Jugadas: {from:int, path:[int], captured:[int]}; `path` = secuencia
completa de casillas de destino (incluye TODOS los saltos de una cadena).

Puro stdlib, sin dependencias.
"""

from __future__ import annotations

AGENT_PLAYER = 2
HUMAN_PLAYER = 1

EMPTY, P1, P2, KING1, KING2 = 0, 1, 2, 17, 18

_GLYPH = {EMPTY: ".", P1: "o", P2: "x", KING1: "O", KING2: "X"}


def is_king(code: int) -> bool:
    return code in (KING1, KING2)


def owner(code: int) -> int:
    """0/1/2 según quién ocupa la casilla (damas incluidas)."""
    if code in (P1, KING1):
        return 1
    if code in (P2, KING2):
        return 2
    return 0


def cell_name(idx: int) -> str:
    """'r3c5' = fila 3 (0 arriba), columna 5 (0 izquierda)."""
    return "r%d c%d" % (idx // 8, idx % 8)


def render(cells) -> str:
    """ASCII del tablero 8x8 con eje de filas/columnas, listo para un prompt."""
    if len(cells) != 64:
        raise ValueError("cells debe tener 64 enteros, tiene %d" % len(cells))
    head = "     " + " ".join(str(c) for c in range(8))
    lines = [head]
    for y in range(8):
        row = " ".join(_GLYPH[int(cells[y * 8 + x])] for x in range(8))
        lines.append("  y%d  %s" % (y, row))
    lines.append("  (leyenda: o/x fichas humano(P1)/agente(P2), O/X damas, . vacía; índice = y*8+x)")
    return "\n".join(lines)


def moves_for_prompt(moves) -> list:
    """Descripciones legibles: '21->17 (1 captura)' con coordenadas."""
    out = []
    for m in moves:
        frm = int(m["from"])
        path = [int(p) for p in m.get("path", [])]
        cap = len(m.get("captured", []) or [])
        dest = path[-1] if path else frm
        desc = "%d->%d (%s)" % (frm, dest, cell_name(dest))
        if len(path) > 1:
            desc += " cadena " + "-".join(str(p) for p in path)
        if cap:
            desc += " [%d captura%s]" % (cap, "" if cap == 1 else "s")
        out.append(desc)
    return out


def find_move(moves, from_i, to_i=None, path=None):
    """Empareja la elección del modelo contra la lista legal de Godot.

    Acepta {from,to} (destino final) o {from,path}. `to` coincide con
    path[-1]; si varias jugadas comparten destino, gana la de más capturas.
    Devuelve la jugada CANONICA completa (con su path y captured) o None.
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
    """Cuerpo exacto para checkers_play_move: {from, path} (ver Contrato)."""
    return {"from": int(move["from"]), "path": [int(p) for p in move["path"]]}


def parse_move_json(text: str):
    """Extracción robusta de la jugada pedida al LLM.

    Acepta JSON puro, cercas ```json, o JSON embutido en prosa. Busca el
    primer objeto con enteros "from" y ("to" o "path"). Devuelve dict
    {'from':int,'to':int?,'path':[int]?} o None si no hay nada util.
    """
    if not text:
        return None
    import json as _json
    import re
    candidates = []
    # 1) todo el texto como JSON
    candidates.append(text.strip())
    # 2) bloque ```...```
    for block in re.findall(r"```(?:json)?\s*(.+?)```", text, flags=re.S):
        candidates.append(block.strip())
    # 3) primer {...} anidado-correcto-ish en prosa
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
