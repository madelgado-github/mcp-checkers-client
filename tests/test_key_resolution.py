#!/usr/bin/env python3
"""Resolución de la key de Qwen: orden env > formulario (memoria) > fichero
legacy, y scrub de TODAS las keys vistas. Con valores FAKE: nunca se usa una
key real en los tests y se comprueba que nada se escribe en disco."""

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

    def test_env_gana_sobre_todo(self):
        os.environ[key_source.ENV_NAME] = FAKE_ENV
        self._write_legacy(FAKE_FILE)
        self.holder.set(FAKE_FORM)          # aunque el formulario también tenga
        key, src = key_source.resolve_key(self.holder, self.key_file, self.log,
                                          form_timeout=1)
        self.assertEqual((key, src), (FAKE_ENV, "env"))
        self.assertIn("variable de entorno", "\n".join(self.log.lines))

    def test_formulario_si_no_hay_env(self):
        self._write_legacy(FAKE_FILE)
        self.holder.set(FAKE_FORM)          # key ya pegada en la página
        key, src = key_source.resolve_key(self.holder, self.key_file, self.log,
                                          form_timeout=1)
        self.assertEqual((key, src), (FAKE_FORM, "form"))

    def test_formulario_llega_tarde(self):
        def late():
            import time
            time.sleep(0.3)
            self.holder.set(FAKE_FORM)
        threading.Thread(target=late, daemon=True).start()
        key, src = key_source.resolve_key(self.holder, "/sin/fichero", self.log,
                                          form_timeout=5)
        self.assertEqual((key, src), (FAKE_FORM, "form"))

    def test_fichero_legacy_ultimo_recurso_y_aviso(self):
        self._write_legacy(FAKE_FILE)
        key, src = key_source.resolve_key(self.holder, self.key_file, self.log,
                                          allow_form=False)   # sin servidor (stub)
        self.assertEqual((key, src), (FAKE_FILE, "file"))
        joined = "\n".join(self.log.lines)
        self.assertIn("AVISO (desaconsejado)", joined)

    def test_sin_nada_error_claro_sin_fichero(self):
        files_before = set(os.listdir(self.tmp))
        with self.assertRaises(AgentError) as ctx:
            key_source.resolve_key(self.holder, self.key_file, self.log,
                                   form_timeout=0.3)
        msg = str(ctx.exception)
        self.assertIn(key_source.ENV_NAME, msg)   # dice qué hacer
        self.assertIn("memoria", msg)
        self.assertEqual(files_before, set(os.listdir(self.tmp)))  # cero disco

    def test_fichero_legacy_vacio_error(self):
        with open(self.key_file, "w", encoding="utf-8") as fh:
            fh.write("   \n")
        with self.assertRaises(AgentError):
            key_source.resolve_key(self.holder, self.key_file, self.log,
                                   allow_form=False)

    def test_scrub_maska_todas_las_keys_vistas(self):
        os.environ[key_source.ENV_NAME] = FAKE_ENV
        key_source.resolve_key(self.holder, "/sin/fichero", self.log,
                               form_timeout=0)
        self.holder.set(FAKE_FORM)                # y llegó otra por el formulario
        text = "error con " + FAKE_ENV + " y " + FAKE_FORM
        scrubbed = self.holder.scrub(text)
        self.assertNotIn(FAKE_ENV, scrubbed)
        self.assertNotIn(FAKE_FORM, scrubbed)
        self.assertEqual(scrubbed.count(key_source.MASK), 2)

    def test_holder_set_vacio_rechaza(self):
        with self.assertRaises(ValueError):
            self.holder.set("   ")


class TestGameLoggerScrub(unittest.TestCase):
    """El logger con scrub=holder.scrub no deja escapar la key al fichero."""

    def test_log_file_no_contiene_key(self):
        from game_session import GameLogger
        tmp = tempfile.mkdtemp()
        try:
            holder = key_source.KeyHolder()
            log_path = os.path.join(tmp, "game.log")
            log = GameLogger(log_path, echo=False, scrub=holder.scrub)
            holder.set(FAKE_FORM)
            log("recibida la key %s del formulario" % FAKE_FORM)
            with open(log_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            self.assertNotIn(FAKE_FORM, content)
            self.assertIn(key_source.MASK, content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
