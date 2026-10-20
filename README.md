# webmcp-agent — Play Checkers (Damas) against an LLM over WebMCP, no agent CLI required

A local script that mounts the MCP bridge, generates the pairing token, opens
the **pairing page** (not the game: you open the board yourself) and plays the
**P2 seat turn by turn with an LLM** (Qwen Cloud, OpenAI-compatible API). The
human plays P1 in the browser; the agent sees the board through the
`checkers_*` tools of the Checkers page itself.

The board is the Checkers demo on the public site **tabandblank.com** — the
single game channel of this tool. The only localhost involved is plumbing:
the WebMCP relay (`ws://localhost:4797`) and the local pairing/token server
(`http://localhost:8088/_pairing`). You always play against the site.

Local files: `config.ini`, `game.log`, `PAIRING.html` and `qwen.key` are in
`.gitignore` — never committed. The Qwen API key **must not live in a file**:
see "The Qwen key" below (`qwen.key` remains only as an optional, discouraged
legacy path; if a key ever lived in that file, rotate it).

## Requirements

- Windows (the script handles the daemon's Windows gotcha; on POSIX it uses
  DEVNULL and works the same).
- Python 3.11+ (`py` / `python`; tested on 3.13). **Pure stdlib**: no pip, no
  Playwright, no deps. The bridge `npx -y @jason.today/webmcp` is started by
  the script itself: **it is not a Python dependency**.
- Node/npx on PATH (for the bridge above).
- A current Chromium/Firefox browser for the game tab.
- The Checkers page with the `checkers_*` tools: the production channel
  (`https://tabandblank.com/games/checkers/`) works as-is — pairing is valid
  from ANY origin.

## Usage

```powershell
# 1) (optional) copy and edit the local config
copy config.example.ini config.ini
# 2) the API key, in THIS order (see "The Qwen key" below):
#    a) environment variable:  $env:QWEN_API_KEY = "sk-..."   (current session)
#    b) or paste it in the key box of the pairing page (step 3b of the flow)
py play_checkers.py
```

What you will see: the relay daemon starts (4797), the MCP bridge runs over
stdio, the token is generated, the local pairing/token server comes up on
8088 and **ONLY the pairing page `http://localhost:8088/_pairing` opens in
your browser** — the token, the key box and the 2 board links are there. In
the terminal, on a prominent banner of ITS OWN: the pairing URL and **the
token printed** (single use; handy over SSH or headless — the API key is
NEVER printed). The **board does not open by default**: pick it from
`/_pairing` itself or from the terminal links, or use `--open prod` to open
both tabs. Then it waits for the key (if there is no `QWEN_API_KEY`, the key
box on `/_pairing`), waits for pairing, and plays. Full decision log:
`game.log` (key masked wherever it comes from; the token only goes to
screen, never into the log).

Flags: `--serve-only` (stack up + token, no game loop; the pairing server and
the key box stay live — **it also opens `/_pairing`**, the same flag silences
it), `--no-pairing-browser` (do NOT open anything in the browser, neither in
normal mode nor in `--serve-only`: for SSH or headless; the pairing URL and
the token are still printed), `--undo N` (remove N human+agent pairs and
exit), `--model M`, `--max-turns N`, `--difficulty easy|medium|hard|expert`,
`--open prod|none` (opt-in to open the **board** tabs of the public site;
**default `none`**: links are only listed — the pairing page always opens
unless `--no-pairing-browser`), `--no-browser` (compat: same as `--open none`;
does not affect the `/_pairing` auto-open), `--player llm|heuristic` (local
heuristic without spending LLM calls), `--pair-timeout S`, `--key-timeout S`
(slack waiting for the key in the box), `--stub` (loop demo against
`stub_bridge.py`, no browser; useful to test the LLM path without a real game
— no pairing server runs: the key must come from `QWEN_API_KEY` or the legacy
`key_file`).

## The local server on 8088 (token page only — not the game)

The script always starts a small http.server on 8088. It exists ONLY to
deliver:

- `GET /_pairing` — the pairing page (token + Qwen key box + board links),
- `POST /qwen-key` — the key box endpoint (process memory only),
- `GET /` — a pointer page to `/_pairing`.

Everything else 404s, `/games/checkers/` included: **the game is never served
locally by this tool.** The port stays 8088 for historic reasons; it is not a
game host and you do not play through it.

## Contract (the `checkers_*` tools)

After ticking "Allow agent" and connecting the token in the WebMCP widget,
the page exposes **6 tools** (exact names):

| tool | what it does |
|---|---|
| `checkers_get_state` | board snapshot: `cells`, `state`, `winner`… |
| `checkers_get_legal_moves` | legal moves for the agent's seat |
| `checkers_play_move` | plays: body **`{from, path}`** — NOT `{from,to}`; the page validates it against its own rules |
| `checkers_new_game` | new game (silences the internal AI: `agent_mode`) |
| `checkers_undo` | removes one human+agent pair |
| `checkers_wait_my_turn` | waits for your turn (push + backup polling); `timeout:true` is NOT an error (re-wait), `cancelled:true` = the human unchecked the box |

- **Board**: `cells` is an array of **64 ints**, index `y*8+x` (row y=0 on
  top). Codes: `0` empty, `1` P1 piece (human), `2` P2 piece (agent),
  `17` P1 king, `18` P2 king.
- **Moves**: `{from:int, path:[int], captured:[int]}`; `path` = the full
  sequence of destination cells (includes EVERY jump of a chain).
- **State**: `state` = playing/paused/won/lost; `winner` 1=human, 2=agent.
- **Channel-prefixed names**: tools arrive prefixed by the page's host
  (`<host>-checkers_*`, with `.` and `:` turned into `_`). This agent
  detects them by **SUFFIX** `checkers_*`, so pairing works from any origin.

## The Qwen key: resolution order (no secrets on disk by default)

Implemented in `key_source.py`. The key is looked for in this order:

1. **`QWEN_API_KEY` environment variable** (recommended). On Windows:
   ```powershell
   # current console only:
   $env:QWEN_API_KEY = "sk-..."
   # persistent for your user (reopen the console afterwards):
   setx QWEN_API_KEY "sk-..."
   ```
   (Avoid putting it in the PowerShell profile in plain text on a shared
   machine; `setx` stores it in your user registry.) **An environment
   variable never travels to repositories and is never committed**: that is
   the difference from the file.
2. **Key box on the pairing page** (`http://localhost:8088/_pairing`): the
   `<input type=password>` does `POST /qwen-key` to the script's own server
   and the key stays **in process memory only** — never on disk, never in the
   log (every key seen passes through the `KeyHolder` scrubber before
   reaching `game.log`/console), never returned in the response. Page
   feedback: "saved for this session (memory only)". The loop start waits for
   this event with a timeout (`--key-timeout`, default 300 s); if nothing
   arrives and there is no env or legacy file, it ends with a message that
   says exactly what to do.
3. **OPTIONAL LEGACY, DISCOURAGED**: `[qwen] key_file = qwen.key` in the
   config. Used only if the file exists, and the log says so. If any key ever
   traveled through this file, **rotate it**.

**Rotating the key in Qwen Cloud**: in the token-plan console (DashScope /
Qwen Cloud → API-KEYs), delete the old key and generate a new one; update
`QWEN_API_KEY` (or paste the new one in the key box). The token-plan key is
the one that works against
`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`;
the Coding-plan key (`coding.dashscope…`) gives 401 there.

## Board links (the board never opens by itself)

The script opens **only `/_pairing`** automatically. The board is opened by
hand from that page or from the terminal list (or with `--open prod`):

| channel | EN | ES |
|---|---|---|
| production | `https://tabandblank.com/games/checkers/` | `https://tabandblank.com/es/juegos/checkers/` |

Pairing **works from any origin**: the client already looks for tools by
SUFFIX `checkers_*` (nothing hardcodes a host; verified in
`mcp_stdio.find_checkers_tools` and covered by the suite with a simulated
channel). The channel prefix is set by the daemon from `window.location.host`
with `.` and `:` replaced by `_` (verified in the package source: `_format()`
in `webmcp.js` + `${channel.slice(1)}-${name}` in `websocket-server.js`). So
on the public site the tools come as **`tabandblank_com-checkers_*`** (dots
become underscores: it is NOT `tabandblank.com-…`).

`--open prod` opens both board tabs (EN + ES) of the public site (the pairing
page already opened at launch; it is not duplicated).

## Manual flow for the human

1. Run `py play_checkers.py` (with `QWEN_API_KEY` in the environment, or keep
   the key handy to paste at step 3b).
2. **The pairing page `http://localhost:8088/_pairing` opens in your
   browser** (token + key box + board links). If you are on SSH, launched
   `--no-pairing-browser`, or the tab did not stick: the URL and the token
   are also on their own banner lines in the terminal.
3. From that same page (or the terminal list) open the board:
   `https://tabandblank.com/games/checkers/` (see "Board links" above). With
   `--open prod` the board opens automatically too. In the game tab, tick
   **«Allow agent»** next to the board → the blue WebMCP widget appears.
3b. Paste the API key in the `/_pairing` key box and press "Save key" →
   "saved for this session (memory only)" (skip this if you used
   `QWEN_API_KEY`).
4. Copy the token from `/_pairing` ("Copy token" button) and paste it EXACTLY
   into the widget's field → **Connect**.
5. Wait: the terminal confirms "paired: 6 tools" and the game starts (the
   script sends `new_game`, which silences the internal AI = `agent_mode`).
6. Play your turns on the board; the agent replies in ~1 s (turn push) plus
   the LLM time: **~2 s with `enable_thinking=false`** (default), 2-3 min if
   you enable reasoning (see "Speed").

## Speed: `enable_thinking` (the #1 reason for "the agent never answers")

The token-plan endpoint **reasons by default** with `qwen3.x` family models
(e.g. `qwen3.8-flash`): 2-3 min per real move (measured; reproduced: with
`enable_thinking=true` the same call did not answer within 240 s). With
`"enable_thinking": false` in the body the answer takes **~2 s** (measured
live: 1.96 s with the chess-player prompt signature). The module default is
`false`; enable it only if you prefer stronger opponents at the price of
minutes per move (`llm_timeout` to 180+ then).

- Config: `[qwen] enable_thinking = false|true` (empty = flag not sent, for
  endpoints that do not know it) and `[agent] llm_timeout = 90` per call.
- Plan models (real listing via `GET /models`): `qwen3.6/3.8-flash`,
  `qwen3.7-plus/max`, `qwen3.8-max`, `glm-5.2/5.3`, `deepseek-v4-flash/pro`
  (+`auto`). `qwen3.7-plus` = good quality/speed balance; `qwen3.8-max` =
  strongest, slowest.
- Testing raw speed, no game:

```powershell
# lists the plan models with your key (PowerShell):
curl https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/models `
  -H "Authorization: Bearer $env:QWEN_API_KEY"
# (if you use the legacy key_file: -H "Authorization: Bearer $(Get-Content .\qwen.key)")
# in bash/Git-Bash:
curl https://…/compatible-mode/v1/models -H "Authorization: Bearer $QWEN_API_KEY"
```

## How it works (verified in the source, v0.1.12/0.1.13)

- **Bridge**: `npx -y @jason.today/webmcp@latest --mcp` is an MCP stdio server
  (`@modelcontextprotocol/sdk`, `StdioServerTransport`). The framing is
  **JSON-RPC 2.0 newline-delimited (ndjson), NOT HTTP Content-Length**. It
  talks to the package's WebSocket daemon (port 4797) over the `/mcp` path
  with the server token from `~/.webmcp/.env`.
- **Pairing token**: shown by **the bridge itself** as local tool
  `_webmcp_get-token`: `tools/call` → text with the base64 of
  `{"server":"ws://localhost:4797","token":"<32 hex>"}`; it is registered in
  `~/.webmcp/.webmcp-tokens.json` and is **single use**: when the tab
  registers, the daemon exchanges it for a session token of the page's
  channel. CLI alternative: `--new` prints it to stdout, but if the daemon
  was NOT alive it generates two (the forked child stomps the parent) — that
  is why this script uses the tool.
- **Tools**: they appear in `tools/list` **only after pairing** and arrive
  channel-prefixed (`<formatted-host>-checkers_*`; on the public site:
  `tabandblank_com-checkers_*` — see "Board links"). The script's detection
  is **by suffix** `checkers_*` (`find_checkers_tools`), so it works from any
  origin. The 6 tools and their payloads are detailed in "Contract";
  `play_move` takes **`{from,path}`** (not `{from,to}`) and the page
  validates it against its own rules.
- **Turns**: `wait_my_turn` wakes with the `get_state` snapshot ~1 s after the
  human move (push), with backup polling every 2 s. `timeout:true` is NOT an
  error (re-wait); `cancelled:true` = the human unchecked the box. Board,
  codes and state: see "Contract".

## Troubleshooting

- **The ONLY localhost involved is plumbing**: the WebMCP relay listens on
  `ws://localhost:4797` and the pairing/token server on
  `http://localhost:8088/_pairing`. Neither is a place you play: **the game
  is always the public site tabandblank.com**. The token embeds the relay
  address `ws://localhost:4797` by design — the bridge and your browser live
  on the same machine, so pairing on the site page connects to your local
  relay.
- **Port 8088 busy**: the pairing/token server refuses to start if something
  else holds the port. Free it or change `http_port` in `config.ini`. (This
  port never serves the game, so a same-site server there no longer helps.)
- **Token expired / "Invalid token provided"**: the token is single use.
  Relaunch the script (it regenerates). If you pasted the base64 by hand and
  rewrote it, always copy-paste literally.
- **`--foreground` dies instantly on Windows**: if the daemon's stdin is not
  a TTY, `process.stdin.setRawMode is not a function` (package bug). The
  script starts it in a fresh hidden console (`CREATE_NEW_CONSOLE` +
  SW_HIDE). If you start it by hand, do it in a normal console window.
- **The daemon is shared**: the script reuses a live 4797 and does NOT kill
  it unless it created it (it may be serving other MCP clients). To stop it:
  `npx -y @jason.today/webmcp@latest --quit`.
- **Chrome "blocks localhost" / mixed content**: the token points at
  `ws://localhost:4797` and the relay lives on YOUR machine, while the game
  runs on `https://tabandblank.com` — so the site's tab opening the local
  relay is mixed content (https page → ws:// is not secure) and Chrome may
  ask for "local network access" permission (or block it under the strict
  `chrome://flags/#mixed-content` mode / local-network policies). Verified in
  practice: pairing from the published site WORKS for a human who grants that
  permission. If your build blocks it, check
  `chrome://flags/#allow-insecure-localhost` and the site's local-network
  permission (chrome://settings → the site → "Local network access").
- **The key**: if the loop dies with "no Qwen API key", there was no
  `QWEN_API_KEY`, nobody pasted the `/_pairing` key box within
  `--key-timeout` (300 s) and the legacy `key_file` does not exist. A key
  pasted in the box does NOT survive Ctrl-C (process memory): paste it again
  or use the env var. The log never contains the key (`KeyHolder` scrub over
  env/form/file alike).
- **The agent does not answer**: almost always reasoning is enabled
  (`enable_thinking=true` => 2-3 min/move; see "Speed"). With the default
  `false` it answers in ~2 s. `llm_timeout` (in `[agent]`) and `retries` (in
  `[qwen]`) are set in `config.ini`. The loop prints BEFORE each call
  `querying model ... (timeout Ns, thinking off)` and the duration after; a
  failed retry prints the reason (429/timeout) — it never goes silent. If it
  still fails, the script plays the first legal move and warns — no crash.
- **The relay cuts waits at 30 s**: verified in the package source: two 30 s
  timeouts in series (bridge and daemon) cut ANY `tools/call` at 30 s. That is
  why the script's `wait_seconds` is 20 (<30) and it never uses the page glue
  60 default: with 60 the relay would cut the wait and it would show up as an
  error. (The local glue↔game leg did hold 62.5 s measured live; the cut is
  in the relay/client.)
- **Disconnect at 5 min**: the widget drops on mouse/keyboard inactivity in
  the page (known limitation). Wiggle the mouse now and then; if it drops,
  the script ends with a clear warning and re-pairing is enough.
- **Giving control back to the internal AI**: uncheck the box (the bridge
  sends `set_mode{agent:false}` and cancels waits). There is no `set_mode`
  tool exposed to the agent: unchecking the box is the ONLY route back (that
  is why the flow documents it this way).

## Testing without a browser (loop demo with a real LLM)

```powershell
# the key via environment (stub mode has no pairing page):
$env:QWEN_API_KEY = "sk-..."
py play_checkers.py --stub --player llm --max-turns 2
```

`stub_bridge.py` speaks the same stdio protocol and simulates 3 human→agent
cycles. It does not touch the real game: it only demonstrates the
handshake→wait→prompt→Qwen→`play_move` pipeline. Its channel prefix defaults
to the production one, `tabandblank_com-` (the real channel format), and is
overridable with `WEBMCP_STUB_CHANNEL_PREFIX` to simulate any other channel.

## Tests

```powershell
py -m unittest discover -s tests   # 68 tests, pure stdlib
```

Coverage: board render/validation (the "Contract" section), robust parsing of
the LLM reply, single re-ask + first-legal fallback, key masking, the LLM
body (`enable_thinking` inside ONLY when the config defines it; call/retry
traceability without leaking the key), and the full loop against the stub
(ndjson handshake, 6 tools with the channel prefix, `wait_my_turn`, illegal
move→rejection→re-loop, game over). Plus: key resolution order
env>form>file with fake values and zero disk writes, the server's
`POST /qwen-key` (memory, response and logs without the key, 400/404,
`GET /_pairing`, and every other route 404 — including `/games/checkers/`:
the token server serves no game files), `--open none` never touching
`webbrowser` and the two production links, suffix-based tool discovery with a
simulated channel prefix (mapping and a full game against the stub on an
overridden prefix). Also the auto-open UX: `open_pairing` opens ONLY the
pairing URL by default and `--no-pairing-browser` (used by `--serve-only`
too) never invokes `webbrowser` (monkeypatched like the `--open` tests);
`open_links` opens only board tabs (pairing is not duplicated); and the
served `GET /_pairing` with the real render contains the token +
`<input type=password>` + the 2 production board links.

## Files

| file | role |
|---|---|
| `play_checkers.py` | CLI/orchestrator: daemon, bridge, token, pairing server with `/qwen-key` and `/_pairing`, links, game loop |
| `key_source.py` | API key order (env > in-memory form > legacy file) + scrubber |
| `mcp_stdio.py` | minimal ndjson stdio MCP client (initialize/tools list/call; tools by SUFFIX `checkers_*`) |
| `game_session.py` | turn loop over the 6 tools + logger with masker |
| `qwen_client.py` | Qwen Chat Completions + player prompt + robust picker |
| `board.py` | 64-cell board ↔ prompt text, move matching |
| `stub_bridge.py` | bridge double for tests/demo (no browser; default channel `tabandblank_com-`, env-overridable) |
| `config.example.ini` → `config.ini` | base_url/model/`enable_thinking`/ports/`llm_timeout`/`prod_origin` (local, gitignored) |
| `qwen.key` | **LEGACY, EXPIRING**: the flow does not depend on it (order env > form > file). The config `key_file` only counts if you create the file, and it is discouraged; if a key ever lived there, rotate it in Qwen Cloud |
| `PAIRING.html` | disk copy the script writes of the pairing page (gitignored); the real one is the page served at `/_pairing` (the key box needs HTTP) |
| `game.log` | session decisions (gitignored; keys always masked) |

## Running and bridge dependencies

- The bridge `npx -y @jason.today/webmcp` is **started by the script**; it is
  not a Python dependency. The whole module is stdlib: there is no
  `requirements.txt` and no `pip install`.
- Requirements: **Python 3.11+**, **Node.js** (for npx) and a current
  Chromium/Firefox browser.

## License

MIT — see the [LICENSE](LICENSE) file. © 2026 madelgado-github.
