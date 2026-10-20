#!/usr/bin/env python3
"""Pairing/token server: POST /qwen-key keeps the key IN MEMORY and writes
nothing to disk; GET /_pairing serves the page with the key box. The server
hosts ONLY its own endpoints (/, /_pairing, /qwen-key): everything else 404s
— it no longer serves game files (the board lives on the public site). Fake
values: never a real key in tests."""

import http.server
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import key_source              # noqa: E402
import play_checkers           # noqa: E402

FAKE = "fake-key-for-test-DO-NOT-USE"


class TestPairingHandler(unittest.TestCase):
    def setUp(self):
        self.holder = key_source.KeyHolder()
        self.lines = []
        self.pairing_html = "<html>PAIRING <input id='k'></html>"

        def log(t):
            self.lines.append(str(t))

        handler = play_checkers.make_web_handler(log, holder=self.holder,
                                                 pairing_html=self.pairing_html)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def _get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def _post(self, body, path="/qwen-key", ctype="text/plain"):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=body.encode("utf-8"), method="POST",
                                     headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_post_qwen_key_stores_in_memory_not_disk(self):
        before = set(os.listdir(HERE))
        status, text = self._post(FAKE)
        self.assertEqual(status, 200)
        self.assertIn("saved for this session", text)
        self.assertEqual(self.holder.get(), FAKE)
        # zero writes: the POST does not create any file next to the module
        self.assertEqual(before, set(os.listdir(HERE)))

    def test_response_and_logs_do_not_contain_the_key(self):
        status, text = self._post(FAKE)
        self.assertNotIn(FAKE, text)                 # no echo
        joined = "\n".join(self.lines)
        self.assertNotIn(FAKE, joined)               # not a single log line
        self.assertIn("IN MEMORY", joined)
        # and even if someone logged the key, the holder's scrub masks it
        self.assertNotIn(FAKE, self.holder.scrub("leak " + FAKE))
        self.assertIn(key_source.MASK, self.holder.scrub("leak " + FAKE))

    def test_post_form_urlencoded(self):
        status, _ = self._post("key=" + urllib.parse.quote_plus(FAKE),
                               ctype="application/x-www-form-urlencoded")
        self.assertEqual(status, 200)
        self.assertEqual(self.holder.get(), FAKE)

    def test_post_empty_400_without_overwriting_key(self):
        self.holder.set("first-" + FAKE)
        status, text = self._post("   ")
        self.assertEqual(status, 400)
        self.assertIn("empty", text)
        self.assertEqual(self.holder.get(), "first-" + FAKE)

    def test_post_unknown_route_404(self):
        status, _ = self._post(FAKE, path="/another-route")
        self.assertEqual(status, 404)

    def test_get_own_endpoints_and_404_for_the_rest(self):
        """The token server serves ONLY its own endpoints: /_pairing and /
        answer; any other path — game paths included — is 404 (no web/ is
        served locally: the board is exclusively on the public site)."""
        status, page = self._get("/_pairing")
        self.assertEqual(status, 200)
        self.assertIn("PAIRING", page)
        status, page = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("/_pairing", page)             # pointer to the pairing page
        for dead in ("/index.html", "/games/checkers/", "/es/juegos/checkers/"):
            status, _ = self._get(dead)
            self.assertEqual(status, 404, dead)      # no game files served

    def test_get_pairing_real_has_token_box_and_2_links(self):
        """UX fix 2026-10-20 + production-only flow: /_pairing is the page the
        script opens on its own at launch: it must carry the token, the key box
        and the 2 production board links (EN + ES)."""
        links = play_checkers.build_links("https://tabandblank.com")
        real_html = play_checkers.render_pairing(
            "TOKEN-FAKE-123", links, "http://localhost:8088/_pairing")
        self.server.shutdown()
        self.server.server_close()
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            play_checkers.make_web_handler(lambda t: None,
                                           holder=self.holder,
                                           pairing_html=real_html))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        with urllib.request.urlopen("http://127.0.0.1:%d/_pairing" % self.port,
                                    timeout=5) as r:
            page = r.read().decode("utf-8")
        self.assertIn("TOKEN-FAKE-123", page)          # the token, in plain sight
        self.assertIn('type="password"', page)         # the key box
        self.assertEqual(len(links["prod"]), 2)        # production is the only channel
        for u in links["prod"]:                        # the 2 board links
            self.assertIn(u, page)
        self.assertNotIn("8088/games", page)   # no local board at all

    def test_without_holder_post_is_refused(self):
        """Server without the key box: /qwen-key 404s and the key ends up
        nowhere."""
        self.server.shutdown()
        self.server.server_close()
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            play_checkers.make_web_handler(lambda t: None))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        before = set(os.listdir(HERE))
        status, _ = self._post(FAKE)
        self.assertEqual(status, 404)
        self.assertEqual(before, set(os.listdir(HERE)))


if __name__ == "__main__":
    unittest.main()
