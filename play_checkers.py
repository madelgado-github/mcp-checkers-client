#!/usr/bin/env python3
"""Juega a Damas contra Qwen vía WebMCP sin abrir opencode.

Flujo (ver README.md para el manual del humano y troubleshooting):
  1. levanta/reusa el daemon relay `@jason.today/webmcp` (puerto 4797; en
     Windows SOLO con consola propia: --foreground con stdin de pipe muere
     al instante por process.stdin.setRawMode — verificado en la fuente),
  2. spawnea el puente `--mcp` como subproceso stdio y le habla JSON-RPC 2.0
     (initialize → tools/list → tools/call; framing ndjson, verificado),
  3. pide el token de emparejamiento a la tool local del puente
     `_webmcp_get-token`,
   4. sirve `web/` en 8088 (o reusa si ya está vivo) y ABRE EN EL NAVEGADOR
      SOLO la página de emparejamiento `/_pairing` (token + casilla de la key:
      es lo único que el humano necesita ver al arrancar — FIX UX 2026-10-20).
      El TABLERO NO se abre solo: sus 4 enlaces (local/prod EN/ES) quedan en la
      terminal y en la propia /_pairing; `--open local|prod` opta a abrirlos
      también. `--no-pairing-browser` silencia incluso la auto-apertura de
      /_pairing (SSH o sin navegador): la URL del pairing y el token (de un
      solo uso) se imprimen igualmente por pantalla, prominentes; la key NUNCA
      se imprime. PAIRING.html se sirve además en `/_pairing` del propio
      servidor con la casilla de la API key (POST /qwen-key → solo en memoria
      del proceso; nunca disco, nunca log),
  5. resuelve la key de Qwen: env `QWEN_API_KEY` > formulario (espera con
     timeout) > fichero legacy `key_file` (desaconsejado) — ver key_source.py,
  6. espera a que tools/list muestre las 6 `checkers_*` (pestaña emparejada).
     El emparejamiento vale desde CUALQUIER origen: la detección de tools es
     por SUFIJO `checkers_*`, no por el prefijo de canal del host de la página,
  7. `new_game` (activa agent_mode: la IA interna se silencia — la tool
     `set_mode` NO está expuesta al agente, solo los 6 nombres del glue),
  8. bucle wait_my_turn(20 s) → get_legal_moves → Qwen → play_move hasta
     game over. Ctrl-C: mata puente/daemon propio y cierra el servidor; para
     devolver el turno a la IA interna hay que desmarcar el checkbox (única
     ruta real de `set_mode{agent:false}`).

Uso:
    python play_checkers.py [--serve-only] [--undo N]
        [--model M] [--max-turns N] [--difficulty easy|medium|hard|expert]
        [--open local|prod|none] [--no-pairing-browser] [--no-browser]
        [--player llm|heuristic] [--pair-timeout S] [--key-timeout S]
"""

from __future__ import annotations

import argparse
import configparser
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import key_source          # noqa: E402
import mcp_stdio           # noqa: E402
import qwen_client         # noqa: E402
from game_session import (AgentError, GameLogger, GameSession, HeuristicPlayer)  # noqa: E402

DEFAULTS = {
    "qwen": {"base_url": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
             "model": "qwen3.8-flash",
             "key_file": "qwen.key",
             # token-plan razona por defecto con qwen3.8-flash: 2-3 min/jugada.
             # enable_thinking=false => ~2 s medidos en vivo (ver README).
             "enable_thinking": "false",
             "retries": "1"},
    "webmcp": {"port": "4797"},
    "site": {"http_port": "8088", "web_dir": "web",
             "game_path": "/games/checkers/",
             "game_path_es": "/es/juegos/checkers/",
             "prod_origin": "https://tabandblank.com"},
    "agent": {"difficulty": "medium", "wait_seconds": "20", "max_turns": "120",
              "llm_timeout": "90"},
}


# ---------------------------------------------------------------- utilidades
def load_config(path):
    cp = configparser.ConfigParser()
    cp.read_dict(DEFAULTS)
    if os.path.exists(path):
        cp.read(path, encoding="utf-8")
    return cp


def parse_optional_bool(cp, section, option):
    """True/False, o None con valor vacío/none/null => NO se manda el flag
    (para endpoints que no conocen enable_thinking)."""
    raw = cp.get(section, option, fallback="").strip().lower()
    if raw in ("", "none", "null"):
        return None
    try:
        return cp.getboolean(section, option)
    except ValueError:
        raise AgentError("config [%s] %s=%r: usa true/false o déjalo vacío"
                         % (section, option, raw))


def port_open(port, host="localhost"):
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def pid_listening(port):
    """PID que escucha `port` (netstat -ano); None si no hay."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return None
    needle = ":%d" % port
    for line in out.splitlines():
        if needle in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts[-1].isdigit():
                return int(parts[-1])
    return None


def ensure_daemon(port, log):
    """Devuelve (pid_own, True) si este script levantó el daemon."""
    if port_open(port):
        log("daemon webmcp ya vivo en %d: reutilizado" % port)
        return None, False
    if sys.platform == "win32":
        # --foreground necesita stdin TTY: consola nueva oculta (CREATE_NEW_CONSOLE).
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        p = subprocess.Popen(mcp_stdio.npx_webmcp("--foreground"),
                             creationflags=subprocess.CREATE_NEW_CONSOLE,
                             startupinfo=si)
        pid = p.pid
    else:
        p = subprocess.Popen(mcp_stdio.npx_webmcp("--foreground"),
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid = p.pid
    end = time.time() + 30
    while not port_open(port) and time.time() < end:
        time.sleep(0.4)
    if not port_open(port):
        raise AgentError("el daemon webmcp no arrancó en el puerto %d" % port)
    log("daemon webmcp levantado (pid %d, puerto %d)" % (pid, port))
    return pid, True


def stop_daemon(pid_own):
    if pid_own is None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid_own), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------- servidor web
def make_web_handler(web_dir, log, holder=None, pairing_html=None):
    """Handler: estáticos de `web_dir` + GET /_pairing + POST /qwen-key.

    POST /qwen-key deja la key SOLO en `holder` (memoria del proceso): nunca
    se escribe a disco, nunca se devuelve en la respuesta, nunca se loggea
    (el body jamás aparece en el log; y el propio log pasa por holder.scrub).
    Sin `holder` el servidor se comporta como un SimpleHTTPRequestHandler
    normal (solo estáticos)."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=web_dir, **kw)

        def _respond(self, code, text, ctype="text/plain; charset=utf-8"):
            data = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if pairing_html is not None and self.path.rstrip("/") == "/_pairing":
                self._respond(200, pairing_html, "text/html; charset=utf-8")
                return
            super().do_GET()

        def do_POST(self):
            if holder is None or self.path.rstrip("/") != "/qwen-key":
                self._respond(404, "ruta desconocida")
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 16384)
                raw = self.rfile.read(length).decode("utf-8", "replace")
            except (ValueError, OSError):
                self._respond(400, "lectura fallida")
                return
            body = raw.strip()
            if body.startswith("key="):           # submit de un <form> clásico
                body = urllib.parse.unquote_plus(body[4:]).strip()
            if not body:
                log("POST /qwen-key: submission vacía: rechazada")
                self._respond(400, "vacío: pega la key y reintenta")
                return
            holder.set(body)
            log("qwen key guardada EN MEMORIA por el formulario "
                "(nunca en disco, nunca en log)")
            self._respond(200, "guardada para esta sesión (solo en memoria)")

        def log_message(self, fmt, *args):
            # solo método + ruta + código: el body de /qwen-key jamás se imprime
            log("%s %s" % (self.address_string(), fmt % args))

    return Handler


def serve_web(web_dir, port, game_path, log, holder=None, pairing_html=None):
    """Levanta http.server en 8088 (con el formulario de la key si hay
    `holder`) o reusa uno vivo si ya sirve esta web. Devuelve None en reuso:
    quien llama pasa entonces a file:/// para PAIRING y debe avisar de que
    el POST /qwen-key NO llegará a este proceso (usar QWEN_API_KEY)."""
    if port_open(port):
        try:
            with urllib.request.urlopen("http://localhost:%d%s" % (port, game_path),
                                        timeout=3) as r:
                head = r.read(4096).decode("utf-8", "replace")
            if "webpuzzle-call" in head or "tabandblank" in head:
                log("puerto %d ya sirve la web del proyecto: reutilizado "
                    "(el formulario de la key no llegará a este proceso: "
                    "usa la variable de entorno QWEN_API_KEY)" % port)
                return None
        except Exception:
            pass
        raise AgentError("el puerto %d está ocupado por otro servidor "
                         "(ciérralo o cambia http_port)" % port)
    handler = make_web_handler(web_dir, log, holder=holder, pairing_html=pairing_html)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log("sirviendo %s en http://localhost:%d" % (web_dir, port))
    return httpd


# ---------------------------------------------------------------- pairing
PAIRING_TEMPLATE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>WebPuzzle · Emparejar agente Damas</title><style>
body{font-family:system-ui;max-width:680px;margin:40px auto;padding:0 16px;line-height:1.5}
code,textarea{background:#f2f2f2;padding:2px 6px;border-radius:6px}
textarea{width:100%;height:90px;font-family:monospace}
button{font-size:1rem;padding:8px 18px;border-radius:8px;border:1px solid #888;cursor:pointer}
input[type=password]{font-family:monospace;padding:6px 8px;border-radius:6px;border:1px solid #888;width:60%}
.warn{background:#fff3cd;padding:10px 14px;border-radius:8px}
.keybox{background:#eef7ee;padding:12px 14px;border-radius:8px}
.okmsg{color:#146c2e;font-weight:600} .errmsg{color:#b02a37;font-weight:600}
ul{margin:6px 0}
</style></head><body>
<h1>Empareja el agente con la partida</h1>
<p><b>1.</b> Elige el canal y abre el juego (el emparejamiento funciona desde
cualquiera; las tools salen bajo el prefijo del canal — ver README):</p>
$game_links
<p><b>2.</b> Marca la casilla <b>«Permitir agente» / “Allow agent”</b> (junto al tablero).
Aparece el widget azul de WebMCP.</p>
<p><b>3.</b> Copia este token y pégalo en el campo del widget, botón <i>Connect</i>:</p>
<textarea readonly onclick="this.select()">$token</textarea>
<p><button onclick="copyTok()">Copiar token</button> <span id="ok"></span></p>
<div class="keybox">
<p><b>3b.</b> API key de Qwen (el script la espera antes de jugar). Si no
prefieres la variable de entorno <code>QWEN_API_KEY</code>, pégala aquí:</p>
<input type="password" id="k" placeholder="sk-..." autocomplete="off">
<button onclick="sendKey()">Guardar key</button> <span id="kmsg"></span>
<p style="font-size:.9em">Se queda <b>solo en memoria</b> de este proceso:
nunca se escribe a disco, nunca aparece en el log, nunca se devuelve.
Esta página debe servirla el script (<code>$pairing_http</code>) para que
el botón funcione; abierta como archivo local, usa la variable de entorno.</p>
</div>
<p><b>4.</b> Vuelve a esta terminal: el script detecta solo el emparejamiento
(listará las 6 <code>checkers_*</code>) y empieza la partida.</p>
<div class="warn">⚠ El token es de <b>un solo uso</b>. Si falla, pide otro
(relanza el script). El widget se desconecta tras ~5 min sin mover el ratón
en la página: en partidas largas, toca el ratón de vez en cuando.</div>
<script>
function copyTok(){
  const t=document.querySelector('textarea');
  navigator.clipboard.writeText(t.value).then(()=>{document.getElementById('ok').textContent='¡copiado!';})
  .catch(()=>{t.select();document.execCommand('copy');document.getElementById('ok').textContent='¡copiado! (fallback)';});
}
async function sendKey(){
  const i=document.getElementById('k'), m=document.getElementById('kmsg');
  const v=i.value.trim();
  if(!v){m.textContent='pega antes la key';m.className='errmsg';return;}
  try{
    const r=await fetch('/qwen-key',{method:'POST',
      headers:{'Content-Type':'text/plain'},body:v});
    const t=await r.text();
    i.value='';                      // la página tampoco la retiene
    m.textContent=t;m.className=r.ok?'okmsg':'errmsg';
  }catch(e){
    m.textContent='el servidor local no recibe el POST (¿servidor externo reutilizado o página abierta como archivo?): usa la variable de entorno QWEN_API_KEY';
    m.className='errmsg';
  }
}
</script></body></html>
"""


def build_links(local_base, game_path, game_path_es, prod_origin):
    """Los 4 enlaces del tablero (local EN/ES + producción EN/ES) + pairing."""
    return {
        "local": [local_base + game_path, local_base + game_path_es],
        "prod": [prod_origin + game_path, prod_origin + game_path_es],
    }


def render_pairing(token, links, pairing_http):
    import string
    rows = "".join(
        "<li><a href='%s' target='_blank'>%s</a> <small>[%s]</small></li>" % (u, u, tag)
        for tag, urls in (("local", links["local"]), ("producción", links["prod"]))
        for u in urls)
    return string.Template(PAIRING_TEMPLATE).substitute(
        token=token, game_links="<ul>%s</ul>" % rows, pairing_http=pairing_http)


def write_pairing(html, path):
    """Copia de la página al disco del módulo (gitignored); la versión buena
    es la servida en /_pairing (la casilla de la key necesita HTTP)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


# ---------------------------------------------------------------- main
def open_pairing(pairing_url, no_pairing_browser=False, opener=None, log=print):
    """FIX UX (2026-10-20): al arrancar, en cuanto el server está arriba y el
    token generado, se abre en el navegador por defecto SOLO la página de
    emparejamiento (token + casilla de la key): es lo único que el humano
    necesita ver. El tablero nunca se abre aquí (ver `open_links`). Con
    `--no-pairing-browser` (SSH / sin navegador / CI) no se abre nada: la URL
    sigue impresa por pantalla. Vale también para `--serve-only` (mismo flag
    lo silencia). Devuelve la lista de URLs abiertas (vacía con el flag)."""
    if no_pairing_browser:
        log("--no-pairing-browser: no abro la página de emparejamiento; "
            "ábrela tú donde veas el navegador: %s" % pairing_url)
        return []
    opener = opener or webbrowser.open
    opener(pairing_url)
    log("navegador: abierta la página de emparejamiento: %s" % pairing_url)
    return [pairing_url]


def open_links(mode, links, pairing_url, opener=None, log=print):
    """Pestañas del TABLERO según `--open`: `none` (DEFAULT) no abre el
    tablero y deja los enlaces en la terminal para que el humano elija canal.
    `local`/`prod` abren las dos pestañas (EN/ES) de ese canal. La página de
    emparejamiento NO se toca aquí: se abre sola en el arranque vía
    `open_pairing` (salvo `--no-pairing-browser`). Devuelve la lista de URLs
    del tablero abiertas (vacía en none)."""
    if mode == "none":
        log("--open none (default): no abro el tablero, solo la página de "
            "emparejamiento (arriba). Enlaces del tablero:\n  LOCAL   %s\n  PROD    %s\n  PAIRING %s"
            % ("\n          ".join(links["local"]),
               "\n          ".join(links["prod"]), pairing_url))
        return []
    opener = opener or webbrowser.open
    chosen = list(links[mode])
    for u in chosen:
        opener(u)
    log("navegador: tablero abierto (--open %s): %s" % (mode, ", ".join(chosen)))
    return chosen


def build_argparser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(HERE, "config.ini"))
    ap.add_argument("--serve-only", action="store_true",
                    help="daemon+token+PAIRING+servidor; sin bucle (la web "
                         "sigue en pie: la casilla de la key vive ahí; abre "
                         "/_pairing salvo --no-pairing-browser)")
    ap.add_argument("--undo", type=int, metavar="N", default=None,
                    help="deshace N pares y termina")
    ap.add_argument("--model", default=None, help="modelo Qwen (override de config)")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--difficulty", default=None,
                    choices=["easy", "medium", "hard", "expert"])
    ap.add_argument("--open", default="none", choices=["local", "prod", "none"],
                    help="opt-in: abrir además el TABLERO en el canal elegido "
                         "(default none: solo se listan los enlaces; el "
                         "emparejamiento se abre siempre salvo "
                         "--no-pairing-browser)")
    ap.add_argument("--no-pairing-browser", action="store_true",
                    help="no abrir la página de emparejamiento (/_pairing) en "
                         "el navegador al arrancar (tampoco con --serve-only): "
                         "para SSH o sin navegador; la URL y el token siguen "
                         "imprimiéndose por pantalla; la key NUNCA)")
    ap.add_argument("--no-browser", action="store_true",
                    help="compat: equivale a --open none (hoy es el default; "
                         "no afecta a la auto-apertura de /_pairing: para eso "
                         "está --no-pairing-browser)")
    ap.add_argument("--player", default="llm", choices=["llm", "heuristic"])
    ap.add_argument("--pair-timeout", type=int, default=300,
                    help="segundos esperando el emparejamiento en el navegador")
    ap.add_argument("--key-timeout", type=int, default=300,
                    help="segundos esperando la key de Qwen en el formulario "
                         "de emparejamiento (si no hay QWEN_API_KEY)")
    ap.add_argument("--stub", action="store_true",
                    help="usar stub_bridge.py como puente (demo del bucle sin "
                         "navegador; permite probar el jugador LLM de verdad)")
    return ap


def main(argv=None):
    ap = build_argparser()
    args = ap.parse_args(argv)
    if args.stub and args.serve_only:
        ap.error("--serve-only no tiene sentido con --stub (el stub no sirve web)")
    cfg = load_config(args.config)
    log_path = os.path.join(HERE, "game.log")
    holder = key_source.KeyHolder()
    # la maskara vive en el holder: cubre env, formulario y fichero legacy por
    # igual, desde la primera línea del log (nada de key en game.log ni consola)
    log = GameLogger(log_path, echo=True, scrub=holder.scrub)
    env_key = (os.environ.get(key_source.ENV_NAME) or "").strip()
    if env_key:
        holder.register(env_key)

    qwen_base = cfg.get("qwen", "base_url")
    model = args.model or cfg.get("qwen", "model")
    key_file = cfg.get("qwen", "key_file")
    if not os.path.isabs(key_file):
        key_file = os.path.join(HERE, key_file)
    webmcp_port = cfg.getint("webmcp", "port")
    http_port = cfg.getint("site", "http_port")
    web_dir = cfg.get("site", "web_dir")
    if not os.path.isabs(web_dir):
        web_dir = os.path.join(REPO, web_dir)
    game_path = cfg.get("site", "game_path")
    game_path_es = cfg.get("site", "game_path_es")
    prod_origin = cfg.get("site", "prod_origin")
    difficulty = args.difficulty or cfg.get("agent", "difficulty")
    wait_seconds = cfg.getint("agent", "wait_seconds")
    max_turns = args.max_turns if args.max_turns is not None else cfg.getint("agent", "max_turns")
    llm_timeout = cfg.getint("agent", "llm_timeout")
    enable_thinking = parse_optional_bool(cfg, "qwen", "enable_thinking")
    key = None

    bridge = None
    httpd = None
    pid_own = None
    daemon_owned = False
    exit_code = 0
    try:
        if args.stub:
            bridge = mcp_stdio.McpBridge([sys.executable,
                                          os.path.join(HERE, "stub_bridge.py")])
            bridge.start()
            tools = bridge.find_checkers_tools()
            if len(tools) != 6:
                raise AgentError("el stub no listó las 6 tools")
            if args.player == "llm" and args.undo is None:
                # sin servidor web en modo stub: no hay formulario; env o legacy
                key, _src = key_source.resolve_key(
                    holder, key_file, log, form_timeout=args.key_timeout,
                    allow_form=False)
        else:
            pid_own, daemon_owned = ensure_daemon(webmcp_port, log)
            log("arrancando puente MCP (npx -y @jason.today/webmcp@latest --mcp)...")
            bridge = mcp_stdio.McpBridge(mcp_stdio.npx_webmcp("--mcp"))
            info = bridge.start()
            log("puente MCP listo: %s" % info.get("serverInfo", info))

            token = bridge.get_pairing_token()
            log("token de emparejamiento generado (va en la página /_pairing, no en el repo)")

            local_base = "http://localhost:%d" % http_port
            links = build_links(local_base, game_path, game_path_es, prod_origin)
            pairing_http = local_base + "/_pairing"
            pairing_html = render_pairing(token, links, pairing_http)
            # el http.server SIEMPRE se levanta si el puerto está libre (aunque
            # el humano vaya a jugar a producción): la casilla de la key vive ahí
            httpd = serve_web(web_dir, http_port, game_path, log,
                              holder=holder, pairing_html=pairing_html)
            pairing = write_pairing(pairing_html, os.path.join(HERE, "PAIRING.html"))
            if httpd is None:                       # servidor externo reutilizado
                pairing_url = "file:///" + pairing.replace("\\", "/")
                log("AVISO: servidor externo reutilizado: la casilla de la key "
                    "no llegará a este proceso (POST /qwen-key no disponible): "
                    "define QWEN_API_KEY en la sesión del script y relanza")
            else:
                pairing_url = pairing_http

            # FIX UX 2026-10-20 ("no se abre la página con la casilla y/o el
            # token"): banner prominente en SU LÍNEA + token por pantalla
            # (efímero, de un solo uso: sirve en SSH o sin navegador). La key
            # JAMÁS se imprime (ni pasó por aquí: holder/log con scrub).
            print("\n" + "=" * 64 +
                  "\n>>> Para emparejar y pegar la key, abre en el navegador:\n"
                  ">>>     %s\n"
                  ">>> Token de emparejamiento (UN SOLO USO; relanza para otro):\n"
                  ">>>     %s\n"
                  % (pairing_url, token) + "=" * 64 + "\n", flush=True)
            log("emparejamiento + casilla de la key: %s "
                "(token impreso por pantalla; la key nunca)" % pairing_url)

            # en cuanto el server está arriba y el token generado: se abre
            # SOLO /_pairing (también en --serve-only; --no-pairing-browser
            # lo silencia). El tablero sigue sin abrirse por defecto.
            open_pairing(pairing_url, no_pairing_browser=args.no_pairing_browser,
                         log=log)

            if args.serve_only:
                print("\n--serve-only: stack arriba (daemon+puente + web %d) sin bucle.\n"
                      "Emparejamiento y casilla de la key en (abierta en el navegador "
                      "salvo --no-pairing-browser):\n  %s\n(copia en disco: %s)\n"
                      "Ctrl-C aquí apaga todo." % (http_port, pairing_url, pairing), flush=True)
                while True:
                    time.sleep(2)

            # el tablero NUNCA se abre solo salvo opt-in expreso:
            # --open local|prod añade sus pestañas (emparejamiento ya abierto)
            mode = "none" if args.no_browser else args.open
            open_links(mode, links, pairing_url, log=log)

            if args.player == "llm" and args.undo is None:
                key, _src = key_source.resolve_key(
                    holder, key_file, log, form_timeout=args.key_timeout,
                    allow_form=True)

            log("esperando emparejamiento (máx %ds)..." % args.pair_timeout)
            tools = bridge.wait_paired(timeout=args.pair_timeout, log=log)
            log("emparejado: 6 tools -> %s" % ", ".join(sorted(tools)))

        picker = None
        if args.player == "llm" and args.undo is None:
            client = qwen_client.QwenClient(
                qwen_base, model, key,
                timeout=llm_timeout,
                retries=cfg.getint("qwen", "retries"),
                enable_thinking=enable_thinking,
                on_event=log)  # cada llamada deja: consultando.../duración/motivo del retry
            picker = qwen_client.LLMPicker(client)
            log("jugando %s via %s (thinking %s, llm_timeout %ds)"
                % (model, qwen_base, client._thinking_label(), llm_timeout))
        else:
            picker = HeuristicPlayer()
            log("jugador heurístico local (sin LLM)")

        session = GameSession(bridge, tools, picker, log)
        if args.undo is not None:
            res = session.undo(args.undo)
            log("undo aplicado: %s" % json.dumps(res, ensure_ascii=False)[:300])
            return 0

        session.new_game(difficulty)
        summary = session.run(max_turns=max_turns, wait_seconds=wait_seconds)
        print("\n=== RESULTADO ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    except KeyboardInterrupt:
        log("Ctrl-C: limpiando procesos...")
        log("para devolver el turno a la IA interna: DESMARCA «Permitir agente» "
            "en la página (set_mode no es una tool expuesta; ver ADR).")
        exit_code = 130
        return exit_code
    except (AgentError, mcp_stdio.McpError, qwen_client.QwenError) as e:
        log("ERROR: %s" % e)
        return 1
    finally:
        if bridge is not None:
            bridge.stop()
        if httpd is not None:
            httpd.shutdown()
        if daemon_owned:
            stop_daemon(pid_own)
            log("daemon webmcp detenido")
        elif not args.stub and pid_listening(webmcp_port):
            log("daemon webmcp preexistente en %d: se deja vivo "
                "(pararlo: npx -y @jason.today/webmcp@latest --quit)" % webmcp_port)


if __name__ == "__main__":
    sys.exit(main())
