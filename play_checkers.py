#!/usr/bin/env python3
"""Play Checkers against Qwen over WebMCP, without opening an agent CLI.

Flow (see README.md for the human manual and troubleshooting):
  1. starts/reuses the `@jason.today/webmcp` relay daemon on port 4797 (on
     Windows it needs its OWN console: --foreground with a dead-pipe stdin
     dies instantly via process.stdin.setRawMode - verified in the source),
  2. spawns the `--mcp` bridge as a stdio subprocess and speaks JSON-RPC 2.0
      to it (initialize -> tools/list -> tools/call; ndjson framing, verified),
  3. asks the bridge's local tool `_webmcp_get-token` for the pairing token,
  4. runs the local token server on 8088 and OPENS ONLY the pairing page
     `/_pairing` in the browser (token + API-key box: the only things the
     human needs to see on launch - UX fix 2026-10-20). The server is NOT a
     game host: it serves its own endpoints only (/, /_pairing, /qwen-key;
     everything else 404). The BOARD lives exclusively on the public site
     (https://tabandblank.com): its 2 links (EN/ES) are listed in the
     terminal and on /_pairing itself; `--open prod` opts into opening both
     tabs. `--no-pairing-browser` also silences the /_pairing auto-open (SSH
     or headless): the pairing URL and the single-use token are printed
     prominently anyway; the API key is NEVER printed. PAIRING.html is served
     at `/_pairing` with the API-key box (POST /qwen-key -> process memory
     only: never disk, never log),
  5. resolves the Qwen key: env `QWEN_API_KEY` > form (wait with timeout) >
     legacy `key_file` (discouraged) - see key_source.py,
  6. waits until tools/list shows the 6 `checkers_*` (a tab is paired).
     Pairing works from ANY origin: tool detection is by SUFFIX `checkers_*`,
     not by the channel prefix of the page's host,
  7. `new_game` (activates agent_mode: the internal AI falls silent - the
     `set_mode` tool is NOT exposed to the agent, only the 6 glue names),
  8. loop wait_my_turn(20 s) -> get_legal_moves -> Qwen -> play_move until
     game over. Ctrl-C: kills the bridge/daemon this script owns and closes
     the server; to hand the turn back to the internal AI you must uncheck
     the checkbox (the only real route to `set_mode{agent:false}`).

Usage:
    python play_checkers.py [--serve-only] [--undo N]
        [--model M] [--max-turns N] [--difficulty easy|medium|hard|expert]
        [--open prod|none] [--no-pairing-browser] [--no-browser]
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
sys.path.insert(0, HERE)

import key_source          # noqa: E402
import mcp_stdio           # noqa: E402
import qwen_client         # noqa: E402
from game_session import (AgentError, GameLogger, GameSession, HeuristicPlayer)  # noqa: E402

# The board is ONLY ever on the public site: production is the single game
# channel. The local 8088 server just hosts the pairing/token page.
PROD_GAME_PATHS = ("/games/checkers/", "/es/juegos/checkers/")

DEFAULTS = {
    "qwen": {"base_url": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
             "model": "qwen3.8-flash",
             "key_file": "qwen.key",
             # token-plan reasons by default with qwen3.8-flash: 2-3 min/move.
             # enable_thinking=false => ~2 s measured live (see README).
             "enable_thinking": "false",
             "retries": "1"},
    "webmcp": {"port": "4797"},
    "site": {"http_port": "8088", "prod_origin": "https://tabandblank.com"},
    "agent": {"difficulty": "medium", "wait_seconds": "20", "max_turns": "120",
              "llm_timeout": "90"},
}


# ---------------------------------------------------------------- utilities
def load_config(path):
    cp = configparser.ConfigParser()
    cp.read_dict(DEFAULTS)
    if os.path.exists(path):
        cp.read(path, encoding="utf-8")
    return cp


def parse_optional_bool(cp, section, option):
    """True/False, or None on an empty/none/null value => the flag is NOT sent
    (for endpoints that do not know enable_thinking)."""
    raw = cp.get(section, option, fallback="").strip().lower()
    if raw in ("", "none", "null"):
        return None
    try:
        return cp.getboolean(section, option)
    except ValueError:
        raise AgentError("config [%s] %s=%r: use true/false or leave it empty"
                         % (section, option, raw))


def port_open(port, host="localhost"):
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def pid_listening(port):
    """PID listening on `port` (netstat -ano); None if there is none."""
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
    """Returns (pid_own, True) if this script started the daemon."""
    if port_open(port):
        log("webmcp daemon already listening on port %d: reused" % port)
        return None, False
    if sys.platform == "win32":
        # --foreground needs a TTY stdin: fresh hidden console (CREATE_NEW_CONSOLE).
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
        raise AgentError("the webmcp daemon did not start on port %d" % port)
    log("webmcp daemon started (pid %d, port %d)" % (pid, port))
    return pid, True


def stop_daemon(pid_own):
    if pid_own is None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid_own), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------- token server
INFO_PAGE = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
             "<title>webmcp-agent — local server</title></head><body>"
             "<p>This is webmcp-agent's local token server: it only hosts the "
             "pairing page. It does NOT serve the game — the board lives on "
             "<a href=\"https://tabandblank.com/games/checkers/\">"
             "tabandblank.com</a>.</p>"
             "<p><a href=\"/_pairing\">Go to the pairing page →</a></p>"
             "</body></html>\n")


def make_web_handler(log, holder=None, pairing_html=None):
    """Handler for the local token server: GET / + GET /_pairing +
    POST /qwen-key. Every other route is 404: the board is played on the
    public site, this server exists only to deliver the pairing token and
    the API-key box (no `web/` statics are served).

    POST /qwen-key leaves the key ONLY in `holder` (process memory): it is
    never written to disk, never returned in the response, never logged
    (the body never appears in the log; and the log itself goes through
    holder.scrub). Without `holder` the key box is disabled: /qwen-key 404s."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self, code, text, ctype="text/plain; charset=utf-8"):
            data = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/_pairing" and pairing_html is not None:
                self._respond(200, pairing_html, "text/html; charset=utf-8")
                return
            if path == "/":
                self._respond(200, INFO_PAGE, "text/html; charset=utf-8")
                return
            self._respond(404, "not found: this server only hosts /_pairing "
                               "and /qwen-key (the board lives on the public site)")

        def do_POST(self):
            if holder is None or self.path.rstrip("/") != "/qwen-key":
                self._respond(404, "unknown route")
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 16384)
                raw = self.rfile.read(length).decode("utf-8", "replace")
            except (ValueError, OSError):
                self._respond(400, "read failure")
                return
            body = raw.strip()
            if body.startswith("key="):           # classic <form> submission
                body = urllib.parse.unquote_plus(body[4:]).strip()
            if not body:
                log("POST /qwen-key: empty submission: rejected")
                self._respond(400, "empty: paste the key and try again")
                return
            holder.set(body)
            log("qwen key stored IN MEMORY from the form "
                "(never on disk, never in the log)")
            self._respond(200, "saved for this session (memory only)")

        def log_message(self, fmt, *args):
            # method + path + code only: the /qwen-key body is never printed
            log("%s %s" % (self.address_string(), fmt % args))

    return Handler


def serve_pairing(port, log, holder=None, pairing_html=None):
    """Runs the local token http.server on 8088: /_pairing plus the key-box
    endpoint /qwen-key (when there is a `holder`). It serves no game files.
    Raises if the port is taken."""
    if port_open(port):
        raise AgentError("port %d is already in use by another server "
                         "(close it or change http_port in config.ini)" % port)
    handler = make_web_handler(log, holder=holder, pairing_html=pairing_html)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log("local token server up: http://localhost:%d/_pairing (pairing page "
        "only — the board lives on the public site)" % port)
    return httpd


# ---------------------------------------------------------------- pairing
PAIRING_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>webmcp-agent — Pair your tab</title><style>
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
<h1>Pair the agent with your game</h1>
<p><b>1.</b> Open the board — it always lives on the public site (pairing
works from any origin; the tools appear under the channel prefix, see the
README):</p>
$game_links
<p><b>2.</b> Tick <b>“Allow agent”</b> (next to the board).
The blue WebMCP widget appears.</p>
<p><b>3.</b> Copy this token and paste it into the widget's token field, then
press <i>Connect</i>:</p>
<textarea readonly onclick="this.select()">$token</textarea>
<p><button onclick="copyTok()">Copy token</button> <span id="ok"></span></p>
<div class="keybox">
<p><b>3b.</b> Qwen API key (the script waits for it before playing). If you
would rather not use the <code>QWEN_API_KEY</code> environment variable,
paste it here:</p>
<input type="password" id="k" placeholder="sk-..." autocomplete="off">
<button onclick="sendKey()">Save key</button> <span id="kmsg"></span>
<p style="font-size:.9em">It stays <b>in memory only</b> for this process:
never written to disk, never shown in the log, never returned. This page must
be served by the script (<code>$pairing_http</code>) for the button to work;
opened as a local file, use the environment variable instead.</p>
</div>
<p><b>4.</b> Back to this terminal: the script detects the pairing on its own
(it will list the 6 <code>checkers_*</code>) and the game starts.</p>
<div class="warn">⚠ The token is <b>single use</b>. If it fails, get another
one (relaunch the script). The widget disconnects after ~5 min without mouse
movement on the page: in long games, wiggle the mouse now and then.</div>
<script>
function copyTok(){
  const t=document.querySelector('textarea');
  navigator.clipboard.writeText(t.value).then(()=>{document.getElementById('ok').textContent='copied!';})
  .catch(()=>{t.select();document.execCommand('copy');document.getElementById('ok').textContent='copied! (fallback)';});
}
async function sendKey(){
  const i=document.getElementById('k'), m=document.getElementById('kmsg');
  const v=i.value.trim();
  if(!v){m.textContent='paste the key first';m.className='errmsg';return;}
  try{
    const r=await fetch('/qwen-key',{method:'POST',
      headers:{'Content-Type':'text/plain'},body:v});
    const t=await r.text();
    i.value='';                      // the page does not retain it either
    m.textContent=t;m.className=r.ok?'okmsg':'errmsg';
  }catch(e){
    m.textContent='the local server did not receive the POST: use the QWEN_API_KEY environment variable';
    m.className='errmsg';
  }
}
</script></body></html>
"""


def build_links(prod_origin):
    """The 2 board links (EN + ES), both on the public site. Production is the
    only game channel: the board is never served locally."""
    origin = prod_origin.rstrip("/")
    return {"prod": [origin + p for p in PROD_GAME_PATHS]}


def render_pairing(token, links, pairing_http):
    import string
    rows = "".join(
        "<li><a href='%s' target='_blank'>%s</a> <small>[%s]</small></li>" % (u, u, tag)
        for tag, urls in (("production", links["prod"]),)
        for u in urls)
    return string.Template(PAIRING_TEMPLATE).substitute(
        token=token, game_links="<ul>%s</ul>" % rows, pairing_http=pairing_http)


def write_pairing(html, path):
    """Disk copy of the page (gitignored); the real one is the page served at
    /_pairing (the key box needs HTTP)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


# ---------------------------------------------------------------- main
def open_pairing(pairing_url, no_pairing_browser=False, opener=None, log=print):
    """UX fix (2026-10-20): at launch, as soon as the server is up and the
    token generated, ONLY the pairing page (token + key box) opens in the
    default browser: it is the only thing the human needs to see. The board
    never opens here (see `open_links`). With `--no-pairing-browser` (SSH /
    headless / CI) nothing opens: the URL is still printed. Applies to
    `--serve-only` too (same flag silences it). Returns the list of URLs
    opened (empty with the flag)."""
    if no_pairing_browser:
        log("--no-pairing-browser: not opening the pairing page in a browser; "
            "open it wherever you can see a browser: %s" % pairing_url)
        return []
    opener = opener or webbrowser.open
    opener(pairing_url)
    log("browser: pairing page opened: %s" % pairing_url)
    return [pairing_url]


def open_links(mode, links, pairing_url, opener=None, log=print):
    """BOARD tabs per `--open`: `none` (DEFAULT) opens nothing and leaves the
    production links in the terminal. `prod` opens the 2 tabs (EN + ES) of the
    public site. The pairing page is NOT touched here: it opens on its own at
    launch via `open_pairing` (unless `--no-pairing-browser`). Returns the
    list of board URLs opened (empty on none)."""
    if mode == "none":
        log("--open none (default): the board is not opened automatically, "
            "only the pairing page (above). Board links (public site):\n"
            "  PROD    %s\n  PAIRING %s"
            % ("\n          ".join(links["prod"]), pairing_url))
        return []
    opener = opener or webbrowser.open
    chosen = list(links[mode])
    for u in chosen:
        opener(u)
    log("browser: board opened (--open %s): %s" % (mode, ", ".join(chosen)))
    return chosen


def build_argparser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(HERE, "config.ini"))
    ap.add_argument("--serve-only", action="store_true",
                    help="daemon+token+pairing page+local server; no game "
                         "loop (the key box lives in the server; opens "
                         "/_pairing unless --no-pairing-browser)")
    ap.add_argument("--undo", type=int, metavar="N", default=None,
                    help="undo N human+agent pairs and exit")
    ap.add_argument("--model", default=None, help="Qwen model (config override)")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--difficulty", default=None,
                    choices=["easy", "medium", "hard", "expert"])
    ap.add_argument("--open", default="none", choices=["prod", "none"],
                    help="opt-in: also open the BOARD tabs on the public "
                         "site (default none: the links are only listed; the "
                         "pairing page opens always unless "
                         "--no-pairing-browser)")
    ap.add_argument("--no-pairing-browser", action="store_true",
                    help="do not open the pairing page (/_pairing) in the "
                         "browser at launch (not even with --serve-only): "
                         "for SSH or headless runs; the URL and the token are "
                         "still printed; the key NEVER is)")
    ap.add_argument("--no-browser", action="store_true",
                    help="compat: same as --open none (which is already the "
                         "default; does not affect the /_pairing auto-open: "
                         "for that use --no-pairing-browser)")
    ap.add_argument("--player", default="llm", choices=["llm", "heuristic"])
    ap.add_argument("--pair-timeout", type=int, default=300,
                    help="seconds waiting for the browser tab to pair")
    ap.add_argument("--key-timeout", type=int, default=300,
                    help="seconds waiting for the Qwen key in the pairing "
                         "page box (when there is no QWEN_API_KEY)")
    ap.add_argument("--stub", action="store_true",
                    help="use stub_bridge.py as the bridge (loop demo without "
                         "a browser; lets you test the LLM player for real)")
    return ap


def main(argv=None):
    ap = build_argparser()
    args = ap.parse_args(argv)
    if args.stub and args.serve_only:
        ap.error("--serve-only makes no sense with --stub (the stub serves no web)")
    cfg = load_config(args.config)
    log_path = os.path.join(HERE, "game.log")
    holder = key_source.KeyHolder()
    # the masker lives in the holder: it covers env, form and legacy file
    # alike, from the very first log line (no key in game.log or console)
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
                raise AgentError("the stub did not list the 6 tools")
            if args.player == "llm" and args.undo is None:
                # no local server in stub mode: no form; env or legacy file
                key, _src = key_source.resolve_key(
                    holder, key_file, log, form_timeout=args.key_timeout,
                    allow_form=False)
        else:
            pid_own, daemon_owned = ensure_daemon(webmcp_port, log)
            log("starting MCP bridge (npx -y @jason.today/webmcp@latest --mcp)...")
            bridge = mcp_stdio.McpBridge(mcp_stdio.npx_webmcp("--mcp"))
            info = bridge.start()
            log("MCP bridge ready: %s" % info.get("serverInfo", info))

            token = bridge.get_pairing_token()
            log("pairing token generated (it goes in the /_pairing page, not in the repo)")

            links = build_links(prod_origin)
            pairing_http = "http://localhost:%d/_pairing" % http_port
            pairing_html = render_pairing(token, links, pairing_http)
            # the local token server ALWAYS runs (even when you play on the
            # public site): the key box and /_pairing live there
            httpd = serve_pairing(http_port, log, holder=holder,
                                  pairing_html=pairing_html)
            pairing = write_pairing(pairing_html, os.path.join(HERE, "PAIRING.html"))
            pairing_url = pairing_http

            # UX fix 2026-10-20 ("the page with the box and/or the token did
            # not open"): prominent banner on ITS OWN LINES + token on screen
            # (ephemeral, single use: useful over SSH or headless). The key is
            # NEVER printed (it never even came through here: holder/log scrub).
            print("\n" + "=" * 64 +
                  "\n>>> To pair your tab and paste the key, open in a browser:\n"
                  ">>>     %s\n"
                  ">>> Pairing token (SINGLE USE; relaunch for a new one):\n"
                  ">>>     %s\n"
                  % (pairing_url, token) + "=" * 64 + "\n", flush=True)
            log("pairing + key box: %s "
                "(token printed on screen; the key never)" % pairing_url)

            # as soon as the server is up and the token generated: ONLY
            # /_pairing opens (also under --serve-only; --no-pairing-browser
            # silences it). The board is still never opened by default.
            open_pairing(pairing_url, no_pairing_browser=args.no_pairing_browser,
                         log=log)

            if args.serve_only:
                print("\n--serve-only: stack up (daemon+bridge + token server on %d), no game loop.\n"
                      "Pairing page and key box at (opened in the browser unless "
                      "--no-pairing-browser):\n  %s\n(disk copy: %s)\n"
                      "Ctrl-C here shuts everything down." % (http_port, pairing_url, pairing), flush=True)
                while True:
                    time.sleep(2)

            # the board NEVER opens on its own without an explicit opt-in:
            # --open prod adds its 2 tabs (pairing already opened)
            mode = "none" if args.no_browser else args.open
            open_links(mode, links, pairing_url, log=log)

            if args.player == "llm" and args.undo is None:
                key, _src = key_source.resolve_key(
                    holder, key_file, log, form_timeout=args.key_timeout,
                    allow_form=True)

            log("waiting for pairing (max %ds)..." % args.pair_timeout)
            tools = bridge.wait_paired(timeout=args.pair_timeout, log=log)
            log("paired: 6 tools -> %s" % ", ".join(sorted(tools)))

        picker = None
        if args.player == "llm" and args.undo is None:
            client = qwen_client.QwenClient(
                qwen_base, model, key,
                timeout=llm_timeout,
                retries=cfg.getint("qwen", "retries"),
                enable_thinking=enable_thinking,
                on_event=log)  # each call logs: querying.../duration/retry reason
            picker = qwen_client.LLMPicker(client)
            log("playing %s via %s (thinking %s, llm_timeout %ds)"
                % (model, qwen_base, client._thinking_label(), llm_timeout))
        else:
            picker = HeuristicPlayer()
            log("local heuristic player (no LLM)")

        session = GameSession(bridge, tools, picker, log)
        if args.undo is not None:
            res = session.undo(args.undo)
            log("undo applied: %s" % json.dumps(res, ensure_ascii=False)[:300])
            return 0

        session.new_game(difficulty)
        summary = session.run(max_turns=max_turns, wait_seconds=wait_seconds)
        print("\n=== RESULT ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    except KeyboardInterrupt:
        log("Ctrl-C: cleaning up processes...")
        log("to hand the turn back to the internal AI: UNCHECK “Allow agent” "
            "on the page (set_mode is not an exposed tool; see the README).")
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
            log("webmcp daemon stopped")
        elif not args.stub and pid_listening(webmcp_port):
            log("pre-existing webmcp daemon on %d: left running "
                "(to stop it: npx -y @jason.today/webmcp@latest --quit)" % webmcp_port)


if __name__ == "__main__":
    sys.exit(main())
