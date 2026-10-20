# webmcp-agent — Damas contra un LLM vía WebMCP (sin opencode)

Script local que monta el puente MCP, genera el token de emparejamiento, abre
la **página de emparejamiento** (no la partida: el tablero lo eliges tú) y
juega el puesto de **P2 turno a turno con un LLM** (Qwen Cloud,
API OpenAI-compatible). El humano juega P1 en el navegador; el agente ve el
tablero por las tools `checkers_*` de la propia página de Damas.

El tablero es la demo de Damas del sitio público **tabandblank.com** (o una
copia local de esa web, si prefieres jugar en `localhost`).

Uso local: `config.ini`, `game.log`, `PAIRING.html` y `qwen.key` están en
`.gitignore` — no se commitean nunca. La API key de Qwen **no debe vivir en un
fichero**: ver «La key de Qwen» abajo (`qwen.key` queda solo como legacy
opcional y desaconsejado; si alguna key llegó a vivir en ese fichero, rótala).

## Requisitos

- Windows (el script gestiona el gotcha del daemon en Windows; en POSIX usa
  DEVNULL y funciona igual).
- Python 3.11+ (`py` / `python`; probado con 3.13). **Puro stdlib**: sin pip,
  sin Playwright, sin deps. El puente `npx -y @jason.today/webmcp` lo arranca
  el propio script: **no es una dependencia de Python**.
- Node/npx en PATH (por el puente anterior).
- Navegador Chromium/Firefox actual para la pestaña del juego.
- Una página de Damas con las tools `checkers_*`: el canal de producción
  (`https://tabandblank.com/games/checkers/`) funciona tal cual — el
  emparejamiento vale desde CUALQUIER origen. Para canal local, apunta
  `[site] web_dir` a una copia generada de esa web (esta web NO se incluye en
  este repo).

## Uso

```powershell
# 1) (opcional) copia y edita la config local
copy config.example.ini config.ini
# 2) la API key, por ESTE orden (ver sección «La key de Qwen»):
#    a) variable de entorno:  $env:QWEN_API_KEY = "sk-..."   (sesión actual)
#    b) o la pegas en la casilla de la página de emparejamiento (paso 3 del flujo)
py play_checkers.py
```

Qué verás: arranca el daemon relay (4797), el puente MCP por stdio, genera el
token, sirve la web en 8088 y **ABRE EN EL NAVEGADOR SOLO la página de
emparejamiento `http://localhost:8088/_pairing`** — token, casilla de la key y
los 4 enlaces del tablero están ahí. En la terminal, en SU LÍNEA prominente:
la URL del pairing y **el token impreso** (es de un solo uso; útil por SSH o
sin navegador — la API key NUNCA se imprime). El **tablero no se abre solo**:
elige canal desde la propia `/_pairing` o desde los enlaces de la terminal, o
usa `--open local|prod` para que se abra. Después espera la key (si no hay
`QWEN_API_KEY`, la casilla de `/_pairing`), espera el emparejamiento y juega.
Log de decisiones completo: `game.log` (con la key maskada, venga de donde
venga; el token solo va a pantalla, no al log).

Flags: `--serve-only` (stack arriba y token, sin bucle; la web y el
formulario de la key siguen en pie — **también abre `/_pairing`**, el mismo
flag lo silencia), `--no-pairing-browser` (NO abrir nada en el navegador ni
en modo normal ni en `--serve-only`: para SSH o sin navegador; la URL del
pairing y el token salen por pantalla igualmente), `--undo N` (retira N pares
humano+agente y sale), `--model M`, `--max-turns N`, `--difficulty
easy|medium|hard|expert`, `--open local|prod|none` (opt-in para abrir el
**tablero** en el canal elegido; **default `none`**: solo lista enlaces — el
emparejamiento se abre siempre salvo `--no-pairing-browser`), `--no-browser`
(compat: igual que `--open none`; no afecta a la auto-apertura de `/_pairing`),
`--player llm|heuristic` (heurística local sin gastar LLM),
`--pair-timeout S`, `--key-timeout S` (margen esperando la key en el
formulario), `--stub` (demo del bucle contra `stub_bridge.py`, sin navegador;
útil para probar el camino LLM sin partida real — sin servidor web: la key
debe venir de `QWEN_API_KEY` o del legacy `key_file`).

## Contrato (tools `checkers_*`)

La página expone **6 tools** (nombres exactos) tras marcar «Permitir agente» y
conectar el token en el widget de WebMCP:

| tool | qué hace |
|---|---|
| `checkers_get_state` | snapshot del tablero: `cells`, `state`, `winner`… |
| `checkers_get_legal_moves` | jugadas legales del puesto del agente |
| `checkers_play_move` | juega: body **`{from, path}`** — NO `{from,to}`; la web la valida contra sus reglas |
| `checkers_new_game` | partida nueva (silencia la IA interna: `agent_mode`) |
| `checkers_undo` | retira un par humano+agente |
| `checkers_wait_my_turn` | espera tu turno (push + sondeo de respaldo); `timeout:true` NO es error (re-esperar), `cancelled:true` = el humano desmarcó el checkbox |

- **Tablero**: `cells` es un array de **64 enteros**, índice `y*8+x` (fila
  y=0 arriba). Códigos: `0` vacía, `1` ficha P1 (humano), `2` ficha P2
  (agente), `17` dama P1, `18` dama P2.
- **Jugadas**: `{from:int, path:[int], captured:[int]}`; `path` = secuencia
  completa de casillas de destino (incluye TODOS los saltos de una cadena).
- **Estado**: `state` = playing/paused/won/lost; `winner` 1=humano, 2=agente.
- **Nombres con prefijo de canal**: las tools llegan prefijadas por el host de
  la página (`<host>-checkers_*`, con `.` y `:` convertidos en `_`). Este
  agente las detecta por **SUFIJO** `checkers_*`, así que el emparejamiento
  vale desde cualquier origen (local o producción).

## La key de Qwen: orden de resolución (sin secretos en disco por defecto)

Implementado en `key_source.py`. La key se busca en este orden:

1. **Variable de entorno `QWEN_API_KEY`** (recomendado). En Windows:
   ```powershell
   # solo para la consola actual:
   $env:QWEN_API_KEY = "sk-..."
   # persistente para tu usuario (reabre la consola después):
   setx QWEN_API_KEY "sk-..."
   ```
   (Evita ponerla en el perfil PowerShell en claro si compartes la máquina;
   `setx` la guarda en el registro de tu usuario.) **Una variable de entorno
   no viaja a repositorios ni se commitea**: es la diferencia con el fichero.
2. **Casilla en la página de emparejamiento** (`http://localhost:8088/_pairing`):
   el `<input type=password>` hace `POST /qwen-key` al propio servidor del
   script y la key queda **solo en memoria del proceso** — nunca en disco,
   nunca en el log (todas las keys vistas pasan por el scrubber del `KeyHolder`
   antes de llegar a `game.log`/consola), nunca se devuelve en la respuesta.
   Feedback en la página: «guardada para esta sesión (solo en memoria)». El
   arranque del bucle espera este evento con timeout (`--key-timeout`, default
   300 s); si no llega y no hay env ni fichero legacy, termina con un mensaje
   que dice exactamente qué hacer. Con la página abierta como `file://`
   (servidor externo reutilizado) el POST no llega a este proceso: usa la env.
3. **LEGACY opcional, desaconsejado**: `[qwen] key_file = qwen.key` del
   config. Solo se usa si el fichero existe, y se avisa en el log. Si alguna
   key transitó por este fichero, **rótala**.

**Rotación de la key en Qwen Cloud**: en la consola del plan de tokens
(DashScope / Qwen Cloud → API-KEYs), elimina la key antigua y genera una
nueva; actualiza `QWEN_API_KEY` (o pega la nueva en el formulario). La key
del plan de tokens es la que responde en
`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`;
la del plan Coding (`coding.dashscope…`) da 401 aquí.

## Enlaces del tablero (el tablero nunca se abre solo)

El script abre automáticamente **solo `/_pairing`**. El tablero se abre a mano
desde esa página o desde la lista de la terminal (o con `--open local|prod`):

| canal | EN | ES |
|---|---|---|
| local | `http://localhost:8088/games/checkers/` | `http://localhost:8088/es/juegos/checkers/` |
| producción | `https://tabandblank.com/games/checkers/` | `https://tabandblank.com/es/juegos/checkers/` |

El emparejamiento **funciona desde cualquier origen**: el cliente ya busca
las tools por SUFIJO `checkers_*` (nada hardcodea `localhost_8088`; verificado
en `mcp_stdio.find_checkers_tools` y probado en la suite con un canal
simulado). El prefijo de canal lo pone el daemon con
`window.location.host` sustituyendo `.` y `:` por `_` (verificado en la fuente
del paquete: `_format()` en `webmcp.js` + `${channel.slice(1)}-${name}` en
`websocket-server.js`). Por eso:

- local `http://localhost:8088` → tools `localhost_8088-checkers_*`
- producción `https://tabandblank.com` → tools **`tabandblank_com-checkers_*`**
  (los puntos se convierten en guiones bajos: NO es `tabandblank.com-…`)

`--open local|prod` abre además el **tablero** del canal que elijas (la página
de emparejamiento ya se abrió sola en el arranque; no se duplica).

## Flujo manual del humano

1. Ejecuta `py play_checkers.py` (con `QWEN_API_KEY` en el
   entorno, o ten la key a mano para pegarla en el paso 3b).
2. **Se abre sola en el navegador la página de emparejamiento
   `http://localhost:8088/_pairing`** (token + casilla de la key + enlaces del
   tablero). Si vas por SSH, lanzaste `--no-pairing-browser` o la pestaña no
   cuajó: la URL y el token salen también en SU LÍNEA de la terminal.
3. Desde esa misma página (o la lista de la terminal) abre el tablero que
   quieras: local (`http://localhost:8088/games/checkers/`) o producción
   (`https://tabandblank.com/games/checkers/`; ver «Enlaces» arriba). Con
   `--open local|prod` el tablero se abre solo también. En la pestaña del
   juego, marca **«Permitir agente»** («Allow agent» en la página EN) junto al
   tablero → aparece el widget azul de WebMCP.
3b. Pega la API key en la casilla de `/_pairing` y pulsa «Guardar key» →
   «guardada para esta sesión (solo en memoria)» (omite este paso si usaste
   `QWEN_API_KEY`).
4. Copia el token de `/_pairing` (botón «Copiar token») y pégalo EXACTO en el
   campo del widget → **Connect**.
5. Espera: la terminal confirma «emparejado: 6 tools» y arranca la partida
   (el script manda `new_game`, que silencia la IA interna = `agent_mode`).
6. Juega tus turnos en el tablero; el agente responde en ~1 s (push del turno)
   + el tiempo del LLM: **~2 s con `enable_thinking=false`** (default), 2-3 min
   si activas el razonamiento (ver «Velocidad»).

## Velocidad: `enable_thinking` (el motivo #1 de «el agente no responde»)

El endpoint del plan de tokens **razona por defecto** con modelos de la
familia `qwen3.x` (p. ej. `qwen3.8-flash`): 2-3 min por jugada real (medido;
reproducido: con `enable_thinking=true` la misma llamada no llegó a responder
en 240 s). Con `"enable_thinking": false` en el body la respuesta tarda
**~2 s** (medido en vivo: 1.96 s con la firma del prompt del ajedrecista). El
default del módulo es `false`; actívalo solo si prefieres rivales más fuertes
a cambio de minutos por jugada (`llm_timeout` entonces a 180+).

- Config: `[qwen] enable_thinking = false|true` (vacío = no se manda el flag,
  para endpoints que no lo conocen) y `[agent] llm_timeout = 90` por llamada.
- Modelos del plan (listado real vía `GET /models`): `qwen3.6/3.8-flash`,
  `qwen3.7-plus/max`, `qwen3.8-max`, `glm-5.2/5.3`, `deepseek-v4-flash/pro`
  (+`auto`). `qwen3.7-plus` = buena relación calidad/velocidad;
  `qwen3.8-max` = la más fuerte, más lenta.
- Probar velocidad suelta, sin partida:

```powershell
# lista los modelos del plan con tu key (PowerShell):
curl https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/models `
  -H "Authorization: Bearer $env:QWEN_API_KEY"
# (si usas el legacy key_file: -H "Authorization: Bearer $(Get-Content .\qwen.key)")
# en bash/Git-Bash:
curl https://…/compatible-mode/v1/models -H "Authorization: Bearer $QWEN_API_KEY"
```

## Cómo funciona (verificado en la fuente, v0.1.12/0.1.13)

- **Puente**: `npx -y @jason.today/webmcp@latest --mcp` es un server MCP stdio
  (`@modelcontextprotocol/sdk`, `StdioServerTransport`). El framing es
  **JSON-RPC 2.0 newline-delimited (ndjson), NO Content-Length HTTP**. Habla
  con el daemon WebSocket del paquete (puerto 4797) por la ruta `/mcp` con el
  token de servidor de `~/.webmcp/.env`.
- **Token de emparejamiento**: lo muestra **el propio puente** como tool
  local `_webmcp_get-token`: `tools/call` → texto con el base64 de
  `{"server":"ws://localhost:4797","token":"<32 hex>"}`; se registra en
  `~/.webmcp/.webmcp-tokens.json` (canal `/localhost_4797`) y es **de un solo
  uso**: al registrarse la pestaña, el daemon lo canjea por un token de sesión
  del canal `/localhost_8088` (el host de la página). Alternativa CLI:
  `--new` lo imprime por stdout, pero si el daemon NO estaba vivo genera dos
  (el fork hijo pisa al padre) — por eso este script usa la tool.
- **Tools**: aparecen en `tools/list` **solo tras emparejar** y llegan
  prefijadas por canal (`<host-formatado>-checkers_*`; local:
  `localhost_8088-checkers_*`, producción: `tabandblank_com-checkers_*` —
  ver «Enlaces»). La detección del script es **por sufijo** `checkers_*`
  (`find_checkers_tools`), así que vale desde cualquier origen. Las 6 y sus
  payloads están detallados en la sección «Contrato»; `play_move` toma
  **`{from,path}`** (no `{from,to}`) y la web la valida contra sus reglas.
- **Turnos**: `wait_my_turn` despierta con el snapshot de `get_state` ~1 s
  tras la jugada humana (push), con sondeo de respaldo cada 2 s. `timeout:true`
  NO es error (re-esperar); `cancelled:true` = el humano desmarcó el checkbox.
  Board, códigos y estado: ver «Contrato».

## Troubleshooting

- **Puerto 8088 ocupado**: si quien ocupa es un servidor de la misma web (el
  script lo detecta por el marcador de página y lo reusa). Si es otro,
  libéralo o cambia `http_port` en `config.ini`. `file://` NO vale (el wasm
  no arranca y el DomainGuard exige http). `localhost` pasa el DomainGuard.
- **Token caducado / «Invalid token provided»**: el token es de un solo uso.
  Relanza el script (regenera). Si pegaste a mano el base64 y lo reescribiste,
  siempre copia-pega literal.
- **`--foreground` muere al instante en Windows**: si el stdin del daemon no
  es TTY, `process.stdin.setRawMode is not a function` (bug del paquete). El
  script lo arranca en consola nueva oculta (`CREATE_NEW_CONSOLE` + SW_HIDE).
  Si lo levantas a mano, hazlo en una ventana de consola normal.
- **El daemon es compartido**: el script reutiliza un 4797 vivo y NO lo mata
  si no lo creó él (puede estar sirviendo a otros clientes MCP). Para
  apagarlo: `npx -y @jason.today/webmcp@latest --quit`.
- **Chrome «bloquea localhost» / mixed content**: el token apunta a
  `ws://localhost:4797` y el relay vive en TU máquina. Desde
  `http://localhost:8088` no hay mezcla de schemes. **Desde el sitio publicado
  (`https://tabandblank.com`) la pestaña abre `ws://localhost:4797` contra el
  relay local: es mixed content (https → ws:// no seguro) y Chrome puede pedir
  el permiso de «acceso a la red local» (o bloquearlo en el modo estricto de
  `chrome://flags/#mixed-content` / políticas de local network).** En la
  práctica verificada: al humano LE FUNCIONÓ el emparejamiento desde el sitio
  publicado concediendo ese permiso; si en tu build lo corta, usa el canal
  local (`http://localhost:8088/games/checkers/`) o revisa
  `chrome://flags/#allow-insecure-localhost` y los permisos de red local del
  sitio (chrome://settings → el sitio → «Acceso a dispositivos locales»).
- **La key**: si el bucle muere con «no hay API key de Qwen», no había
  `QWEN_API_KEY`, nadie pegó la casilla de `/_pairing` dentro de
  `--key-timeout` (300 s) y no existe el `key_file` legacy. La key pegada en
  el formulario NO sobrevive a Ctrl-C (es memoria del proceso): re-pégala o
  usa la env. Con un servidor externo reutilizado en 8088 el `POST /qwen-key`
  no llega a este proceso: la página lo avisa («el servidor local no recibe el
  POST») — usa `QWEN_API_KEY`. El log nunca contiene la key (scrub del
  `KeyHolder` sobre env/formulario/fichero por igual).
- **El agente no responde**: casi siempre es el razonamiento activado
  (`enable_thinking=true` => 2-3 min/jugada; ver «Velocidad»). Con el default
  `false` responde en ~2 s. `llm_timeout` (en `[agent]`) y `retries` (en
  `[qwen]`) se ajustan en `config.ini`. El bucle imprime ANTES de cada llamada
  `consultando modelo ... (timeout Ns, thinking off)` y al volver la duración;
  un retry fallido imprime el motivo (429/timeout) — nunca queda mudo. Si aun
  así falla todo, el script juega la primera legal y lo avisa — no se crasha.
- **El relay corta las esperas a los 30 s**: verificado en la fuente del
  paquete: dos timeouts de 30 s en serie (puente y daemon) cortan CUALQUIER
  `tools/call` a los 30 s. Por eso el `wait_seconds` del script es 20 (<30) y
  jamás usa el default 60 del glue de la página: con 60 el relay cortaría la
  espera y aparecería como error. (El tramo local glue↔juego sí aguantó 62,5 s
  medido en vivo; el recorte está en el relay/cliente.)
- **Desconexión a los 5 min**: el widget corta por inactividad de
  ratón/teclado en la página (limitación conocida). Mueve el ratón de vez en
  cuando; si cae, el script termina con aviso claro y basta re-emparejar.
- **Devolver el control a la IA interna**: desmarcar el checkbox (el puente
  manda `set_mode{agent:false}` y cancela esperas). No hay tool `set_mode`
  expuesta al agente: desmarcar el checkbox es la ÚNICA ruta para devolver el
  control (por eso el flujo lo documenta así).

## Probar sin navegador (demo del bucle con LLM real)

```powershell
# la key por entorno (en modo stub no hay casilla de emparejamiento):
$env:QWEN_API_KEY = "sk-..."
py play_checkers.py --stub --player llm --max-turns 2
```

`stub_bridge.py` habla el mismo protocolo stdio y simula 3 ciclos humano→
agente. No toca el juego real: solo demuestra el pipeline handshake→espera→
prompt→Qwen→`play_move`. El prefijo de canal es configurable
(`WEBMCP_STUB_CHANNEL_PREFIX`, p. ej. `tabandblank_com-`) para probar la
detección por sufijo con canales distintos del local.

## Tests

```powershell
py -m unittest discover -s tests   # 68 tests, stdlib puro
```

Cubren: render/validación del tablero (sección «Contrato»), parseo robusto de
la respuesta del LLM, re-pregunta única + fallback primera legal, maskara de
la key, el body del LLM (`enable_thinking` dentro solo cuando la config lo
define; trazabilidad de consultas/retries sin key), y el bucle completo contra
el stub (handshake ndjson, 6 tools con prefijo de canal, `wait_my_turn`,
jugada ilegal→rechazo→re-loop, game over). Además: orden de resolución de la
key env>formulario>fichero con valores fake y cero escrituras en disco, el
`POST /qwen-key` del servidor (memoria, respuesta y logs sin la key, 400/404,
`GET /_pairing`), `--open none` no invoca `webbrowser` y los enlaces
local/producción, y el descubrimiento de tools por SUFIJO con un prefijo de
canal simulado tipo `tabandblank_com-checkers_*` (mapeo y partida completa
contra el stub en ese canal). También la UX de auto-apertura: `open_pairing`
con default abre SOLO la URL del pairing y con `--no-pairing-browser` (lo que
usa también `--serve-only`) no invoca `webbrowser` (monkeypatch igual que los
de `--open`); `open_links` abre ya solo el tablero (el pairing no se
duplica); y `GET /_pairing` servido con el render real contiene token +
`<input type=password>` + los 4 enlaces de juego.

## Archivos

| archivo | papel |
|---|---|
| `play_checkers.py` | CLI/orquestador: daemon, puente, token, web con `/qwen-key` y `/_pairing`, enlaces, bucle |
| `key_source.py` | orden de la API key (env > formulario en memoria > fichero legacy) + scrubber |
| `mcp_stdio.py` | cliente MCP stdio ndjson mínimo (initialize/tools list/call; tools por SUFIJO `checkers_*`) |
| `game_session.py` | bucle de turnos sobre las 6 tools + logger con maskara |
| `qwen_client.py` | Qwen Chat Completions + prompt de jugador + picker robusto |
| `board.py` | tablero 64 celdas ↔ texto de prompt, emparejamiento de jugadas |
| `stub_bridge.py` | doble del puente para tests/demo (sin navegador; canal configurable por env) |
| `config.example.ini` → `config.ini` | base_url/modelo/`enable_thinking`/puertos/`llm_timeout`/`prod_origin` (local, gitignored) |
| `qwen.key` | **LEGACY en extinción**: el flujo no depende de él (orden env > formulario > fichero). El config `key_file` solo vale si tú creas el fichero, y es desaconsejado; si una key llegó a vivir ahí, rótala en Qwen Cloud |
| `PAIRING.html` | copia en disco que el script escribe de la página de emparejamiento (gitignored); la buena es la servida en `/_pairing` (necesita HTTP para la casilla de la key) |
| `game.log` | decisiones de la sesión (gitignored; keys siempre maskadas) |

## Ejecución y dependencias del puente

- El puente `npx -y @jason.today/webmcp` **lo arranca el script**; no es una
  dependencia de Python. Todo el módulo es stdlib: no hay `requirements.txt`
  ni `pip install`.
- Requisitos: **Python 3.11+**, **Node.js** (para npx) y un navegador
  Chromium/Firefox actual.

## Licencia

MIT — ver el archivo [LICENSE](LICENSE). © 2026 madelgado-github.
