#!/usr/bin/env python3
"""FIX UX 2026-10-20: al arrancar se abre SOLO /_pairing (open_pairing); el
tablero nunca se abre salvo opt-in `--open local|prod` (open_links). `none`
(default) y `--no-pairing-browser` no invocan webbrowser; los enlaces listados
cubren local EN/ES y producción EN/ES, y /_pairing lleva token, casilla de la
key y los 4 enlaces de juego. Nothing real here, solo URLs y strings."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import play_checkers   # noqa: E402


class Boom:
    """webbrowser sustituto: cualquier intento de abrir = fallo de test."""
    def open(self, *a, **kw):
        raise AssertionError("webbrowser.open no debe invocarse "
                             "(--open none / --no-pairing-browser)")


class Spy:
    """webbrowser sustituto que registra: prueba que el default SÍ abre."""
    def __init__(self):
        self.calls = []

    def open(self, url, *a, **kw):
        self.calls.append(url)
        return True


class TestOpenLinks(unittest.TestCase):
    def setUp(self):
        self.links = play_checkers.build_links(
            "http://localhost:8088", "/games/checkers/", "/es/juegos/checkers/",
            "https://tabandblank.com")
        self.pairing = "http://localhost:8088/_pairing"
        self.logs = []

    def test_none_no_toca_el_navegador(self):
        real = play_checkers.webbrowser
        play_checkers.webbrowser = Boom()
        try:
            opened = play_checkers.open_links("none", self.links, self.pairing,
                                               log=self.logs.append)
        finally:
            play_checkers.webbrowser = real
        self.assertEqual(opened, [])
        joined = "\n".join(self.logs)
        self.assertIn("LOCAL", joined)   # los enlaces salen listados
        self.assertIn("PROD", joined)
        self.assertIn(self.pairing, joined)

    def test_open_local_abre_solo_el_tablero(self):
        """--open opta SOLO el tablero: el pairing se abrió ya en el arranque
        (open_pairing); abrirlo dos veces serían pestañas duplicadas."""
        seen = []
        opened = play_checkers.open_links("local", self.links, self.pairing,
                                          opener=seen.append, log=lambda t: None)
        self.assertEqual(opened, self.links["local"])
        self.assertEqual(seen, self.links["local"])
        self.assertNotIn(self.pairing, seen)

    def test_open_prod_abre_solo_produccion(self):
        seen = []
        opened = play_checkers.open_links("prod", self.links, self.pairing,
                                          opener=seen.append, log=lambda t: None)
        self.assertEqual(opened, self.links["prod"])
        self.assertTrue(all(u.startswith("https://tabandblank.com") for u in seen))

    def test_argparser_default_none(self):
        args = play_checkers.build_argparser().parse_args([])
        self.assertEqual(args.open, "none")
        self.assertEqual(args.key_timeout, 300)
        # por defecto el pairing SÍ se abre: el flag nuevo está apagado
        self.assertFalse(args.no_pairing_browser)

    def test_argparser_flags_nuevos(self):
        args = play_checkers.build_argparser().parse_args(
            ["--serve-only", "--no-pairing-browser"])
        self.assertTrue(args.serve_only)
        self.assertTrue(args.no_pairing_browser)
        args = play_checkers.build_argparser().parse_args(["--open", "local"])
        self.assertEqual(args.open, "local")
        self.assertFalse(args.no_pairing_browser)


class TestOpenPairing(unittest.TestCase):
    """FIX UX: /_pairing (token + casilla) es lo único que se abre solo."""

    def test_default_abre_solo_la_url_del_pairing(self):
        seen = []
        opened = play_checkers.open_pairing("http://localhost:8088/_pairing",
                                            opener=seen.append, log=lambda t: None)
        self.assertEqual(opened, ["http://localhost:8088/_pairing"])
        self.assertEqual(seen, opened)

    def test_flag_no_invoca_webbrowser(self):
        """Humo (lo que hace main con --serve-only o normal + el flag): con
        --no-pairing-browser el módulo NO toca el navegador, pero loguea la
        URL para que el humano la abra a mano (SSH)."""
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

    def test_default_si_invoca_el_webbrowser_del_modulo(self):
        """Sin el flag el default SÍ abre: mismo monkeypatch que los de --open,
        pero por la ruta del módulo (webbrowser.open), no por parámetro."""
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
    def test_cuatro_enlaces(self):
        links = play_checkers.build_links(
            "http://localhost:8088", "/games/checkers/", "/es/juegos/checkers/",
            "https://tabandblank.com")
        self.assertEqual(links["local"],
                         ["http://localhost:8088/games/checkers/",
                          "http://localhost:8088/es/juegos/checkers/"])
        self.assertEqual(links["prod"],
                         ["https://tabandblank.com/games/checkers/",
                          "https://tabandblank.com/es/juegos/checkers/"])

    def test_pairing_page_tiene_casilla_y_enlaces(self):
        links = play_checkers.build_links(
            "http://localhost:8088", "/games/checkers/", "/es/juegos/checkers/",
            "https://tabandblank.com")
        html = play_checkers.render_pairing("TOKEN-FAKE", links,
                                            "http://localhost:8088/_pairing")
        self.assertIn("TOKEN-FAKE", html)
        self.assertIn("/qwen-key", html)                 # el fetch del botón
        self.assertIn('type="password"', html)           # casilla de la key
        self.assertIn("Guardar key", html)
        self.assertIn("solo en", html)
        self.assertIn("memoria", html)                   # promesa de la casilla
        # los 4 enlaces del tablero: quien cae en /_pairing primero (ahora se
        # abre sola) debe poder elegir canal desde la propia página
        for u in links["local"] + links["prod"]:
            self.assertIn(u, html)


if __name__ == "__main__":
    unittest.main()
