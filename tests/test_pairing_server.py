#!/usr/bin/env python3
"""Servidor de emparejamiento: POST /qwen-key deja la key EN MEMORIA y no
escribe nada en disco; GET /_pairing sirve la página con la casilla. Values
fake: nunca una key real en tests."""

import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import key_source              # noqa: E402
import play_checkers           # noqa: E402

FAKE = "fake-key-para-test-NO-USAR"


class TestPairingHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<html>static</html>")
        self.holder = key_source.KeyHolder()
        self.lines = []
        self.pairing_html = "<html>PAIRING <input id='k'></html>"

        def log(t):
            self.lines.append(str(t))

        handler = play_checkers.make_web_handler(self.tmp, log,
                                                 holder=self.holder,
                                                 pairing_html=self.pairing_html)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _post(self, body, path="/qwen-key", ctype="text/plain"):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=body.encode("utf-8"), method="POST",
                                     headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_post_qwen_key_guarda_en_memoria_sin_disco(self):
        before = set(os.listdir(self.tmp))
        status, text = self._post(FAKE)
        self.assertEqual(status, 200)
        self.assertIn("guardada para esta sesi", text)
        self.assertEqual(self.holder.get(), FAKE)
        # cero escrituras: ni index nuevo ni qwen.key
        self.assertEqual(before, set(os.listdir(self.tmp)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "qwen.key")))

    def test_response_y_logs_no_contienen_la_key(self):
        status, text = self._post(FAKE)
        self.assertNotIn(FAKE, text)                 # no se hace eco
        joined = "\n".join(self.lines)
        self.assertNotIn(FAKE, joined)               # ni una línea del log
        self.assertIn("EN MEMORIA", joined)
        # y aunque alguien loggeara la key, el scrub del holder la maska
        self.assertNotIn(FAKE, self.holder.scrub("fuga " + FAKE))
        self.assertIn(key_source.MASK, self.holder.scrub("fuga " + FAKE))

    def test_post_form_urlencoded(self):
        status, _ = self._post("key=" + urllib.parse.quote_plus(FAKE),
                               ctype="application/x-www-form-urlencoded")
        self.assertEqual(status, 200)
        self.assertEqual(self.holder.get(), FAKE)

    def test_post_vacio_400_sin_pisar_key(self):
        self.holder.set("primera-" + FAKE)
        status, text = self._post("   ")
        self.assertEqual(status, 400)
        self.assertIn("vac", text)
        self.assertEqual(self.holder.get(), "primera-" + FAKE)

    def test_post_ruta_desconocida_404(self):
        status, _ = self._post(FAKE, path="/otra-ruta")
        self.assertEqual(status, 404)

    def test_get_pairing_sirve_pagina_y_estaticos_siguen(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/_pairing" % self.port,
                                    timeout=5) as r:
            self.assertIn("PAIRING", r.read().decode("utf-8"))
        with urllib.request.urlopen("http://127.0.0.1:%d/index.html" % self.port,
                                    timeout=5) as r:
            self.assertIn("static", r.read().decode("utf-8"))

    def test_get_pairing_real_contiene_token_casilla_y_4_enlaces(self):
        """FIX UX 2026-10-20: /_pairing es la página que el script abre solo
        al arrancar: debe tener el token, la casilla de la key y los 4 enlaces
        del tablero (local/prod EN/ES) para elegir canal desde ella misma."""
        links = play_checkers.build_links(
            "http://localhost:8088", "/games/checkers/", "/es/juegos/checkers/",
            "https://tabandblank.com")
        real_html = play_checkers.render_pairing(
            "TOKEN-FAKE-123", links, "http://localhost:8088/_pairing")
        self.server.shutdown()
        self.server.server_close()
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            play_checkers.make_web_handler(self.tmp, lambda t: None,
                                           holder=self.holder,
                                           pairing_html=real_html))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        with urllib.request.urlopen("http://127.0.0.1:%d/_pairing" % self.port,
                                    timeout=5) as r:
            page = r.read().decode("utf-8")
        self.assertIn("TOKEN-FAKE-123", page)          # el token, a la vista
        self.assertIn('type="password"', page)          # casilla de la key
        for u in links["local"] + links["prod"]:        # los 4 enlaces de juego
            self.assertIn(u, page)

    def test_sin_holder_no_acepte_post(self):
        """Servidor sin formulario (p. ej. solo estáticos): /qwen-key da 404 y
        la key no acaba en ningún sitio."""
        self.server.shutdown()
        self.server.server_close()
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            play_checkers.make_web_handler(self.tmp, lambda t: None))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        before = set(os.listdir(self.tmp))
        status, _ = self._post(FAKE)
        self.assertEqual(status, 404)
        self.assertEqual(before, set(os.listdir(self.tmp)))


if __name__ == "__main__":
    unittest.main()
