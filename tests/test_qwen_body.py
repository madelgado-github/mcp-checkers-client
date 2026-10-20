#!/usr/bin/env python3
"""Tests for QwenClient's HTTP body: the `enable_thinking` flag is sent ONLY
when the config defines it (latency fix 2026-10-20: token-plan reasons by
default with qwen3.8-flash => 2-3 min/move; with enable_thinking:false ~2 s).

Loop traceability: `on_event` emits 'querying model...' before the call, the
duration after, and the REASON of each failed retry (never goes silent). The
client is FAKED with a patched urlopen: no network, hermetic."""

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
    """Returns (sent_bodies, timeouts, events). side_effect: callable(req)
    that returns a response or raises."""
    captured = {"bodies": [], "timeouts": [], "n": 0}

    def fake_urlopen(req, timeout=None):
        captured["bodies"].append(json.loads(req.data.decode("utf-8")))
        captured["timeouts"].append(timeout)
        captured["n"] += 1
        return side_effect(req)

    events = []
    client.on_event = events.append
    with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
        client.chat([{"role": "user", "content": "hello"}])
    return captured["bodies"], captured["timeouts"], events


def make_client(**kw):
    kw.setdefault("api_key", "SECRET-KEY-123")
    return QwenClient("http://fake.invalid/v1", "qwen3.8-flash", kw.pop("api_key"), **kw)


class TestEnableThinkingBody(unittest.TestCase):
    def test_false_is_included_in_body(self):
        bodies, _to, _ev = _run_chat(make_client(enable_thinking=False))
        self.assertIn("enable_thinking", bodies[0])
        self.assertIs(bodies[0]["enable_thinking"], False)
        # other fields intact
        self.assertEqual(bodies[0]["model"], "qwen3.8-flash")
        self.assertEqual(len(bodies[0]["messages"]), 1)

    def test_true_is_included_in_body(self):
        bodies, _to, _ev = _run_chat(make_client(enable_thinking=True))
        self.assertIs(bodies[0]["enable_thinking"], True)

    def test_null_DOES_NOT_send_flag(self):
        """Endpoints that do not know the flag: with enable_thinking=None the
        body is exactly model+messages (pre-fix behavior)."""
        bodies, _to, _ev = _run_chat(make_client(enable_thinking=None))
        self.assertNotIn("enable_thinking", bodies[0])
        self.assertEqual(sorted(bodies[0].keys()), ["messages", "model"])

    def test_constructor_default_is_none_and_timeout_90(self):
        c = QwenClient("http://x", "m", "k")
        self.assertIsNone(c.enable_thinking)
        self.assertEqual(c.timeout, 90)  # was 180; thinking off => 90 is enough


class TestTraceability(unittest.TestCase):
    def test_events_before_duration_and_thinking_label(self):
        bodies, timeouts, events = _run_chat(make_client(enable_thinking=False, timeout=90))
        self.assertEqual(timeouts, [90])  # the timeout reaches urlopen
        self.assertIn("querying model", events[0])
        self.assertIn("timeout 90s", events[0])
        self.assertIn("thinking off", events[0])
        self.assertIn("responded in", events[-1])
        self.assertIn("s", events[-1])

    def test_event_label_is_no_flag_when_none(self):
        _b, _t, events = _run_chat(make_client(enable_thinking=None))
        self.assertIn("thinking no flag", events[0])

    def test_failed_retry_is_not_silent(self):
        """First attempt URLError (e.g. timeout), second OK: the REASON of the
        failure must be emitted and retried; and the key never shows up in the
        events."""

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
             mock.patch("time.sleep"):  # do not sleep in the backoff
            out = c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(out, '{"from": 1}')
        retry_lines = [e for e in events if "failed" in e]
        self.assertEqual(len(retry_lines), 1)
        self.assertIn("429", retry_lines[0])
        self.assertIn("attempt 1/2", retry_lines[0])
        self.assertNotIn("SECRET-KEY-123", "\n".join(events))  # scrub applied

    def test_401_terminal_emits_abort(self):
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
        self.assertTrue(any("aborting" in e for e in events))


class TestConfigEnableThinking(unittest.TestCase):
    """parse_optional_bool in play_checkers: false/true/empty => False/True/None."""

    def _cp(self, raw):
        import configparser

        import play_checkers as pc
        cp = configparser.ConfigParser()
        cp.read_dict({"qwen": {"enable_thinking": raw}})
        return pc.parse_optional_bool(cp, "qwen", "enable_thinking")

    def test_values(self):
        self.assertIs(self._cp("false"), False)
        self.assertIs(self._cp("true"), True)
        self.assertIsNone(self._cp(""))
        self.assertIsNone(self._cp("none"))

    def test_junk_raises_clear_error(self):
        import play_checkers as pc
        from game_session import AgentError
        with self.assertRaises(AgentError):
            self._cp("maybe")


if __name__ == "__main__":
    unittest.main()
