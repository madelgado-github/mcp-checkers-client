#!/usr/bin/env python3
"""UX fix 2026-10-20 + production-only flow (2026-10-20): on launch ONLY
/_pairing opens (open_pairing); the board never opens unless opted in with
`--open prod` (open_links). `none` (default) and `--no-pairing-browser` never
touch webbrowser; the listed links are the 2 production ones (EN + ES on
tabandblank.com - there is no local board anymore) and /_pairing carries the
token, the key box and those links. Nothing real here, just URLs and strings."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import play_checkers   # noqa: E402


class Boom:
    """webbrowser stand-in: any attempt to open = test failure."""
    def open(self, *a, **kw):
        raise AssertionError("webbrowser.open must not be called "
                             "(--open none / --no-pairing-browser)")


class Spy:
    """webbrowser stand-in that records: proves the default DOES open."""
    def __init__(self):
        self.calls = []

    def open(self, url, *a, **kw):
        self.calls.append(url)
        return True


class TestOpenLinks(unittest.TestCase):
    def setUp(self):
        self.links = play_checkers.build_links("https://tabandblank.com")
        self.pairing = "http://localhost:8088/_pairing"
        self.logs = []

    def test_none_never_touches_the_browser(self):
        real = play_checkers.webbrowser
        play_checkers.webbrowser = Boom()
        try:
            opened = play_checkers.open_links("none", self.links, self.pairing,
                                               log=self.logs.append)
        finally:
            play_checkers.webbrowser = real
        self.assertEqual(opened, [])
        joined = "\n".join(self.logs)
        self.assertIn("PROD", joined)     # the links are still listed
        self.assertIn(self.pairing, joined)

    def test_open_prod_opens_the_two_board_tabs(self):
        """--open opts into the BOARD only: the pairing page already opened at
        launch (open_pairing); opening it twice means duplicate tabs."""
        seen = []
        opened = play_checkers.open_links("prod", self.links, self.pairing,
                                          opener=seen.append, log=lambda t: None)
        self.assertEqual(opened, self.links["prod"])
        self.assertEqual(seen, self.links["prod"])
        self.assertEqual(len(seen), 2)                 # EN + ES
        self.assertNotIn(self.pairing, seen)

    def test_open_prod_only_the_public_domain(self):
        seen = []
        opened = play_checkers.open_links("prod", self.links, self.pairing,
                                          opener=seen.append, log=lambda t: None)
        self.assertEqual(opened, self.links["prod"])
        self.assertTrue(all(u.startswith("https://tabandblank.com") for u in seen))

    def test_argparser_default_none(self):
        args = play_checkers.build_argparser().parse_args([])
        self.assertEqual(args.open, "none")
        self.assertEqual(args.key_timeout, 300)
        # by default the pairing page DOES open: the flag is off
        self.assertFalse(args.no_pairing_browser)

    def test_argparser_flags(self):
        args = play_checkers.build_argparser().parse_args(
            ["--serve-only", "--no-pairing-browser"])
        self.assertTrue(args.serve_only)
        self.assertTrue(args.no_pairing_browser)
        args = play_checkers.build_argparser().parse_args(["--open", "prod"])
        self.assertEqual(args.open, "prod")
        self.assertFalse(args.no_pairing_browser)


class TestOpenPairing(unittest.TestCase):
    """UX fix: /_pairing (token + key box) is the only thing that opens alone."""

    def test_default_opens_only_the_pairing_url(self):
        seen = []
        opened = play_checkers.open_pairing("http://localhost:8088/_pairing",
                                            opener=seen.append, log=lambda t: None)
        self.assertEqual(opened, ["http://localhost:8088/_pairing"])
        self.assertEqual(seen, opened)

    def test_flag_does_not_invoke_webbrowser(self):
        """Smoke (what main does with --serve-only or normal mode + flag): with
        --no-pairing-browser the module does NOT touch the browser, but it logs
        the URL so the human opens it by hand (SSH)."""
        real = play_checkers.webbrowser
        play_checkers.webbrowser = Boom()
        logs = []
        try:
            opened = play_checkers.open_pairing("http://localhost:8088/_pairing",
                                                 no_pairing_browser=True,
                                                 log=logs.append)
        finally:
            play_checkers.webbrowser = real
        self.assertEqual(opened, [])
        self.assertIn("http://localhost:8088/_pairing", "\n".join(logs))

    def test_default_does_invoke_the_module_webbrowser(self):
        """Without the flag the default DOES open: same monkeypatch as the
        --open tests, but through the module path (webbrowser.open), not a
        parameter."""
        real = play_checkers.webbrowser
        spy = Spy()
        play_checkers.webbrowser = spy
        try:
            opened = play_checkers.open_pairing("http://localhost:8088/_pairing",
                                                log=lambda t: None)
        finally:
            play_checkers.webbrowser = real
        self.assertEqual(opened, ["http://localhost:8088/_pairing"])
        self.assertEqual(spy.calls, ["http://localhost:8088/_pairing"])


class TestBuildLinksAndPairing(unittest.TestCase):
    def test_two_production_links(self):
        """Production is the ONLY board channel: exactly 2 links (EN + ES),
        both on tabandblank.com, no localhost among them."""
        links = play_checkers.build_links("https://tabandblank.com")
        self.assertEqual(set(links), {"prod"})
        self.assertEqual(links["prod"],
                         ["https://tabandblank.com/games/checkers/",
                          "https://tabandblank.com/es/juegos/checkers/"])

    def test_pairing_page_has_key_box_and_links(self):
        links = play_checkers.build_links("https://tabandblank.com")
        html = play_checkers.render_pairing("TOKEN-FAKE", links,
                                            "http://localhost:8088/_pairing")
        self.assertIn("TOKEN-FAKE", html)
        self.assertIn("/qwen-key", html)                 # the button's fetch
        self.assertIn('type="password"', html)           # the key box
        self.assertIn("Save key", html)
        self.assertIn("in memory only", html)            # the box's promise
        # the 2 board links: whoever lands on /_pairing first (it opens alone
        # now) must be able to open the board from the page itself
        for u in links["prod"]:
            self.assertIn(u, html)


if __name__ == "__main__":
    unittest.main()
