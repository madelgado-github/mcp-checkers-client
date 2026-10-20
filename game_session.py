#!/usr/bin/env python3
"""Bucle de partida humano-vs-agente sobre las tools `checkers_*`.

El contrato de las tools checkers_* está documentado en el README (sección
Contrato): la lista real de tools del glue de la página de Damas (6 tools,
nombres exactos `checkers_*`; `play_move` recibe {from, path} — NO {from,to}).

Limitación verificada en la fuente del relay (v0.1.12/0.1.13): los dos tramos
(del puente stdio y del daemon) cortan un tools/call a los **30 s**. Por eso
`wait_my_turn` se pide con timeout 20 s (<30) y el loop re-espera ante
`timeout:true`; jamás usa el default de 60 s del glue.
"""

from __future__ import annotations

import time


class AgentError(Exception):
    pass


class HeuristicPlayer:
    """Rival del loop para tests/demo sin gastar LLM: captura máxima o primera."""

    def choose(self, board_text, moves, legal_lines):
        best = max(moves, key=lambda m: len(m.get("captured", []) or []))
        return best, "(heuristic: %s)" % legal_lines[moves.index(best)], None


class GameLogger:
    """Log de decisiones en game.log (sin key: todo texto pasa por scrub)."""

    def __init__(self, path, echo=True, scrub=None):
        self.path = path
        self.echo = echo
        self._scrub = scrub or (lambda s: s)

    def __call__(self, text):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), self._scrub(text))
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        if self.echo:
            print(line, flush=True)


class GameSession:
    """Turnos del agente. `bridge`: objeto con call_tool(name,args,timeout);
    `tools`: mapa nombre_corto->nombre_con_prefijo del canal."""

    def __init__(self, bridge, tools, picker, log):
        self.bridge = bridge
        self.tools = tools
        self.picker = picker
        self.log = log

    # --- helpers de tool ----------------------------------------------------
    def _call(self, short, args=None, timeout=40):
        name = self.tools.get(short)
        if name is None:
            raise AgentError("tool no emparejada: %s" % short)
        return self.bridge.call_tool(name, args or {}, timeout=timeout)

    @staticmethod
    def _data(payload):
        """Los handlers devuelven {ok:true,data:{...}} o {ok:false,error,...}."""
        return payload.get("data") if isinstance(payload, dict) else None

    # --- API de partida -----------------------------------------------------
    def new_game(self, difficulty=None):
        args = {"difficulty": difficulty} if difficulty else {}
        res = self._call("checkers_new_game", args)
        data = self._data(res) or {}
        if not res.get("ok"):
            raise AgentError("new_game: %s" % res.get("error", res))
        self.log("new_game ok (difficulty=%s, sale primero jugador %s)"
                 % (data.get("difficulty", "?"), data.get("current_player")))
        return data

    def undo(self, n=1):
        for i in range(n):
            res = self._call("checkers_undo")
            data = self._data(res) or {}
            self.log("undo %d/%d: undone=%s (retira el par humano+agente)"
                     % (i + 1, n, data.get("undone")))
        return self._call("checkers_get_state")

    def run(self, max_turns=120, wait_seconds=20, max_idle_waits=90):
        """Bcle estrella (F6): wait_my_turn → snapshot → legal_moves → picker →
        play_move, hasta game over, cancelación o tope. Devuelve resumen."""
        import board as B
        played = 0
        idle_waits = 0
        while True:
            # 1) esperar el turno (push ~1 s; timeout < 30 s del relay)
            try:
                w = self._call("checkers_wait_my_turn", {"timeout_seconds": wait_seconds})
            except Exception as e:
                raise AgentError("puente caído o pestaña cerrada: %s" % e)
            data = self._data(w) or {}
            if not w.get("ok"):
                if w.get("error") == "busy":       # >3 esperas: improbable, reintentar
                    time.sleep(2)
                    continue
                raise AgentError("wait_my_turn: %s" % w.get("error"))
            if data.get("cancelled"):
                self.log("el humano desmarcó 'Permitir agente': la IA interna retoma. Fin.")
                return {"reason": "cancelled", "turns": played}
            if data.get("reason") == "game_over" or data.get("state") in ("won", "lost"):
                return self._finish(data, played)
            if data.get("timeout"):
                idle_waits += 1
                if idle_waits % 3 == 1:
                    self.log("el humano no mueve (espera %d x %ds)..." % (idle_waits, wait_seconds))
                if idle_waits >= max_idle_waits:
                    raise AgentError("sin jugada humana en ~%d s: plantón."
                                     % (idle_waits * wait_seconds))
                continue
            idle_waits = 0
            if data.get("current_player") != B.AGENT_PLAYER:
                continue                            # defensivo: aún no es su turno

            # 2) lectura de jugadas legales (ver Contrato)
            lm = self._call("checkers_get_legal_moves")
            if not lm.get("ok"):
                if lm.get("error") == "not_playing":
                    st = self._data(self._call("checkers_get_state")) or {}
                    return self._finish(st, played)
                raise AgentError("get_legal_moves: %s" % lm.get("error"))
            moves = (self._data(lm) or {}).get("moves") or []
            if not moves:
                st = self._data(self._call("checkers_get_state")) or {}
                return self._finish(st, played)

            # 3) decisión (LLM o heurística) y jugada validada por la web
            cells = (data.get("cells") or [0] * 64)
            board_text = B.render(cells)
            legal_lines = B.moves_for_prompt(moves)
            self.log("turno %d: %s\n%s" % (played + 1, " | ".join(legal_lines[:8]), board_text))
            move, raw, warn = self.picker.choose(board_text, moves, legal_lines)
            if warn:
                self.log("AVISO picker: %s" % warn)
            payload = B.canonical_payload(move)
            res = self._call("checkers_play_move", payload)
            d = self._data(res) or {}
            if not res.get("ok"):
                err = res.get("error")
                if err == "not_your_turn":
                    self.log("play_move: not_your_turn (carrera con el humano): re-espero")
                    continue
                if err in ("illegal_move", "busy", "bad_args"):
                    self.log("play_move rechazada (%s): recalcular" % err)
                    continue
                raise AgentError("play_move: %s" % err)
            played += 1
            self.log("jugada %d: %s -> %s capturas=%s (estado %s, winner %s) [%s]"
                     % (played, payload["from"], payload["path"],
                        d.get("captured"), d.get("state"), d.get("winner"),
                        (raw[:80] if isinstance(raw, str) else raw)))
            if d.get("winner") or d.get("state") in ("won", "lost"):
                snap = self._data(self._call("checkers_get_state")) or d
                return self._finish(snap, played)
            if played >= max_turns:
                raise AgentError("máximo de jugadas del agente alcanzado (%d)" % max_turns)

    def _finish(self, snap, played):
        winner = snap.get("winner", 0)
        state = snap.get("state", "?")
        txt = ("FIN: state=%s winner=%s (%s) tras %d jugadas del agente"
               % (state, winner,
                  {1: "gana el humano", 2: "gana el agente"}.get(winner, "sin ganador"),
                  played))
        self.log(txt)
        return {"reason": "game_over", "winner": winner, "state": state, "turns": played}
