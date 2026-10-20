#!/usr/bin/env python3
"""Qwen key resolution: order env > form (memory) > legacy file, and scrub of
ALL keys seen. With FAKE values: a real key is never used in the tests and we
check nothing is written to disk."""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import key_source                      # noqa: E402
from game_session import AgentError    # noqa: E402

FAKE_ENV = "fake-env-key-DO-NOT-USE"
FAKE_FORM = "fake-form-key-DO-NOT-USE"
FAKE_FILE = "fake-file-key-DO-NOT-USE"


class QuietLog:
    def __init__(self):
        self.lines = []

    def __call__(self, text):
        self.lines.append(str(text))


class TestKeyResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmp, "legacy.key")
        self.log = QuietLog()
        self.holder = key_source.KeyHolder()
        patcher = mock.patch.dict(os.environ, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(key_source.ENV_NAME, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_legacy(self, value):
        with open(self.key_file, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")

    def test_env_wins_over_everything(self):
        os.environ[key_source.ENV_NAME] = FAKE_ENV
        self._write_legacy(FAKE_FILE)
        self.holder.set(FAKE_FORM)          # even if the form also has one
        key, src = key_source.resolve_key(self.holder, self.key_file, self.log,
                                          form_timeout=1)
        self.assertEqual((key, src), (FAKE_ENV, "env"))
        self.assertIn("environment variable", "\n".join(self.log.lines))

    def test_form_if_no_env(self):
        self._write_legacy(FAKE_FILE)
        self.holder.set(FAKE_FORM)          # key already pasted on the page
        key, src = key_source.resolve_key(self.holder, self.key_file, self.log,
                                          form_timeout=1)
        self.assertEqual((key, src), (FAKE_FORM, "form"))

    def test_form_arrives_late(self):
        def late():
            import time
            time.sleep(0.3)
            self.holder.set(FAKE_FORM)
        threading.Thread(target=late, daemon=True).start()
        key, src = key_source.resolve_key(self.holder, "/no/file", self.log,
                                          form_timeout=5)
        self.assertEqual((key, src), (FAKE_FORM, "form"))

    def test_legacy_file_last_resource_with_warning(self):
        self._write_legacy(FAKE_FILE)
        key, src = key_source.resolve_key(self.holder, self.key_file, self.log,
                                          allow_form=False)   # no server (stub)
        self.assertEqual((key, src), (FAKE_FILE, "file"))
        joined = "\n".join(self.log.lines)
        self.assertIn("WARNING (discouraged)", joined)

    def test_nothing_at_all_clear_error_without_file(self):
        files_before = set(os.listdir(self.tmp))
        with self.assertRaises(AgentError) as ctx:
            key_source.resolve_key(self.holder, self.key_file, self.log,
                                   form_timeout=0.3)
        msg = str(ctx.exception)
        self.assertIn(key_source.ENV_NAME, msg)   # says what to do
        self.assertIn("memory", msg)
        self.assertEqual(files_before, set(os.listdir(self.tmp)))  # zero disk

    def test_empty_legacy_file_error(self):
        with open(self.key_file, "w", encoding="utf-8") as fh:
            fh.write("   \n")
        with self.assertRaises(AgentError):
            key_source.resolve_key(self.holder, self.key_file, self.log,
                                   allow_form=False)

    def test_scrub_masks_every_key_seen(self):
        os.environ[key_source.ENV_NAME] = FAKE_ENV
        key_source.resolve_key(self.holder, "/no/file", self.log,
                               form_timeout=0)
        self.holder.set(FAKE_FORM)                # and another one arrived via form
        text = "error with " + FAKE_ENV + " and " + FAKE_FORM
        scrubbed = self.holder.scrub(text)
        self.assertNotIn(FAKE_ENV, scrubbed)
        self.assertNotIn(FAKE_FORM, scrubbed)
        self.assertEqual(scrubbed.count(key_source.MASK), 2)

    def test_holder_set_empty_is_refused(self):
        with self.assertRaises(ValueError):
            self.holder.set("   ")


class TestGameLoggerScrub(unittest.TestCase):
    """The logger with scrub=holder.scrub never lets the key out to the file."""

    def test_log_file_does_not_contain_the_key(self):
        from game_session import GameLogger
        tmp = tempfile.mkdtemp()
        try:
            holder = key_source.KeyHolder()
            log_path = os.path.join(tmp, "game.log")
            log = GameLogger(log_path, echo=False, scrub=holder.scrub)
            holder.set(FAKE_FORM)
            log("received the key %s from the form" % FAKE_FORM)
            with open(log_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            self.assertNotIn(FAKE_FORM, content)
            self.assertIn(key_source.MASK, content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
