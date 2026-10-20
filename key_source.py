#!/usr/bin/env python3
"""Resolución del secreto Qwen sin depender de un fichero con la key dentro.

Orden (petición del humano 2026-10-20):
  1. variable de entorno ``QWEN_API_KEY``;
  2. casilla de texto en la página de emparejamiento: POST /qwen-key al
     http.server local → queda SOLO en memoria de este proceso (nunca en
     disco, nunca en log);
  3. LEGACY opcional y desaconsejado: ``[qwen] key_file`` del config (el
     fichero local gitignored), solo si existe.

Nada de esto escribe ni loggea el secreto: todo texto pasa por el scrubber
del `KeyHolder`, que maskara TODAS las keys vistas (env, formulario, fichero).
"""

from __future__ import annotations

import os

from game_session import AgentError

MASK = "***MASKED***"
ENV_NAME = "QWEN_API_KEY"


class KeyHolder:
    """Guarda la(s) key(s) en memoria y sirve de scrubber para el logger.

    - `set(key)`: llegada por el formulario (libera `wait`).
    - `register(key)`: llegada por env/fichero (solo para el maskado).
    - `scrub(text)`: callable compatible con GameLogger(scrub=...)."""

    def __init__(self):
        import threading
        self._event = threading.Event()
        self._key = None
        self._seen = []

    def _remember(self, key):
        if key and key not in self._seen:
            self._seen.append(key)

    def set(self, key):
        key = str(key).strip()
        if not key:
            raise ValueError("key vacía")
        self._remember(key)
        self._key = key
        self._event.set()
        return key

    def register(self, key):
        """Apunta una key ya conocida (env/fichero) para que el scrub la
        maskare desde el primer log, sin activar la espera del formulario."""
        self._remember(str(key).strip() if key else None)

    def wait(self, timeout):
        """Espera una key por el formulario `timeout` s; None si no llega."""
        if self._event.wait(timeout):
            return self._key
        return None

    def get(self):
        return self._key

    def scrub(self, text):
        out = str(text)
        for k in self._seen:
            out = out.replace(k, MASK)
        return out


def resolve_key(holder, key_file, log, form_timeout=300, allow_form=True,
                env_name=ENV_NAME):
    """Aplica el orden env > formulario(memoria) > fichero legacy.

    Devuelve (key, fuente). Lanza AgentError con mensaje accionable si no
    hay ninguna (no hay espera infinita: `form_timeout` + `--key-timeout`).
    """
    # 1) variable de entorno
    env_val = (os.environ.get(env_name) or "").strip()
    if env_val:
        holder.register(env_val)
        log("qwen key: tomada de la variable de entorno %s" % env_name)
        return env_val, "env"

    # 2) formulario de la página de emparejamiento (solo en memoria)
    if allow_form:
        log("qwen key: sin %s — pégala en la casilla de la página de "
            "emparejamiento (%ds de margen; se queda SOLO en memoria de "
            "este proceso) o usa la legacy key_file" % (env_name, form_timeout))
        key = holder.wait(form_timeout)
        if key:
            log("qwen key: recibida por el formulario (memoria; nunca disco/log)")
            return key, "form"
    else:
        key = holder.get()  # en modo stub no hay servidor: solo vale env/fichero
        if key:
            return key, "form"

    # 3) legacy opcional: fichero local (desaconsejado: es un secreto en disco)
    if key_file and os.path.exists(key_file):
        import qwen_client
        try:
            key = qwen_client.load_key(key_file)
        except qwen_client.QwenError as e:
            raise AgentError("qwen key: %s" % holder.scrub(e))
        holder.register(key)
        log("AVISO (desaconsejado): key leída del fichero legacy %s. Rota la "
            "key y usa %s o la casilla de la página de emparejamiento."
            % (key_file, env_name))
        return key, "file"

    raise AgentError(
        "no hay API key de Qwen: ponla en la casilla de la página de "
        "emparejamiento (se queda solo en memoria), o define la variable de "
        "entorno %s (Windows: setx %s \"sk-...\" y reabre la consola; ver "
        "README). El fichero legacy [qwen] key_file sigue valiendo como "
        "último recurso desaconsejado. Nada se ha guardado en disco."
        % (env_name, env_name))
