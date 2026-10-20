#!/usr/bin/env python3
"""Tests del body HTTP de QwenClient: la flag `enable_thinking` se manda SOLO
cuando la config la define (fix latencia 2026-10-20: token-plan razona por
defecto con qwen3.8-flash => 2-3 min/jugada; con enable_thinking:false ~2 s).

Trazabilidad del bucle: `on_event` emite 'consultando modelo...' antes de la
llamada, la duracion al volver, y el MOTIVO de cada retry fallido (no queda
mudo). Cliente FAKED con urlopen parcheado: sin red, hermetico."""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from qwen_client import QwenClient, QwenError  # noqa: E402

MODEL_REPLY = json.dumps({"choices": [{"message": {"content": '{"from": 1}'}}]}).encode("utf-8")


def _ok_response(req=None):
    class R:
        def read(self_inner):
            return MODEL_REPLY

        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

    return R()


def _run_chat(client, side_effect=_ok_response):
    """Devuelve (bodies_enviados, timeouts, eventos). side_effect: callable(req)
    que retorna response o lanza."""
    captured = {"bodies": [], "timeouts": [], "n": 0}

    def fake_urlopen(req, timeout=None):
        captured["bodies"].append(json.loads(req.data.decode("utf-8")))
        captured["timeouts"].append(timeout)
        captured["n"] += 1
        return side_effect(req)

    events = []
    client.on_event = events.append
    with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
        client.chat([{"role": "user", "content": "hola"}])
    return captured["bodies"], captured["timeouts"], events


def make_client(**kw):
    kw.setdefault("api_key", "SECRET-KEY-123")
    return QwenClient("http://fake.invalid/v1", "qwen3.8-flash", kw.pop("api_key"), **kw)


class TestEnableThinkingBody(unittest.TestCase):
    def test_false_se_incluye_en_body(self):
        bodies, _to, _ev = _run_chat(make_client(enable_thinking=False))
        self.assertIn("enable_thinking", bodies[0])
        self.assertIs(bodies[0]["enable_thinking"], False)
        # demas campos intactos
        self.assertEqual(bodies[0]["model"], "qwen3.8-flash")
        self.assertEqual(len(bodies[0]["messages"]), 1)

    def test_true_se_incluye_en_body(self):
        bodies, _to, _ev = _run_chat(make_client(enable_thinking=True))
        self.assertIs(bodies[0]["enable_thinking"], True)

    def test_null_NO_se_envia_flag(self):
        """Endpoints que no conocen el flag: con enable_thinking=None el body
        es exactamente model+messages (comportamiento pre-fix)."""
        bodies, _to, _ev = _run_chat(make_client(enable_thinking=None))
        self.assertNotIn("enable_thinking", bodies[0])
        self.assertEqual(sorted(bodies[0].keys()), ["messages", "model"])

    def test_default_constructor_es_none_y_timeout_90(self):
        c = QwenClient("http://x", "m", "k")
        self.assertIsNone(c.enable_thinking)
        self.assertEqual(c.timeout, 90)  # antes 180; thinking off => sobra 90


class TestTrazabilidad(unittest.TestCase):
    def test_eventos_antes_duracion_y_label_thinking(self):
        bodies, timeouts, events = _run_chat(make_client(enable_thinking=False, timeout=90))
        self.assertEqual(timeouts, [90])  # el timeout llega a urlopen
        self.assertIn("consultando modelo", events[0])
        self.assertIn("timeout 90s", events[0])
        self.assertIn("thinking off", events[0])
        self.assertIn("respondi\u00f3 en", events[-1])
        self.assertIn("s", events[-1])

    def test_evento_label_sin_flag_cuando_none(self):
        _b, _t, events = _run_chat(make_client(enable_thinking=None))
        self.assertIn("thinking sin flag", events[0])

    def test_retry_fallido_no_queda_mudo(self):
        """Primer intento URLError (p.ej. timeout), segundo OK: debe emitirse
        el MOTIVO del fallo y reintentarse; y la key jamas sale en los eventos."""

        def flaky(req):
            if "first" not in flaky.__dict__:
                flaky.first = True
                raise urllib.error.URLError("429 Too Many Requests SECRET-KEY-123")
            return _ok_response()

        c = make_client(enable_thinking=False, retries=1)
        c.on_event = None
        events = []
        c.on_event = events.append
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=lambda req, timeout=None: flaky(req)), \
             mock.patch("time.sleep"):  # no dormir en el backoff
            out = c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(out, '{"from": 1}')
        retry_lines = [e for e in events if "fall\u00f3" in e]
        self.assertEqual(len(retry_lines), 1)
        self.assertIn("429", retry_lines[0])
        self.assertIn("intento 1/2", retry_lines[0])
        self.assertNotIn("SECRET-KEY-123", "\n".join(events))  # scrub aplicado

    def test_401_terminal_emite_aborto(self):
        c = make_client(retries=2)
        events = []
        c.on_event = events.append

        def deny(req):
            raise urllib.error.HTTPError("http://fake.invalid", 401, "Unauthorized",
                                         {}, None)

        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=lambda req, timeout=None: deny(req)):
            with self.assertRaises(QwenError):
                c.chat([{"role": "user", "content": "x"}])
        self.assertTrue(any("aborto" in e for e in events))


class TestConfigEnableThinking(unittest.TestCase):
    """parse_optional_bool de play_checkers: false/true/vacío => False/True/None."""

    def _cp(self, raw):
        import configparser

        import play_checkers as pc
        cp = configparser.ConfigParser()
        cp.read_dict({"qwen": {"enable_thinking": raw}})
        return pc.parse_optional_bool(cp, "qwen", "enable_thinking")

    def test_valores(self):
        self.assertIs(self._cp("false"), False)
        self.assertIs(self._cp("true"), True)
        self.assertIsNone(self._cp(""))
        self.assertIsNone(self._cp("none"))

    def test_junk_gira_error_claro(self):
        import play_checkers as pc
        from game_session import AgentError
        with self.assertRaises(AgentError):
            self._cp("quizas")


if __name__ == "__main__":
    unittest.main()
