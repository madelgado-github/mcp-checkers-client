#!/usr/bin/env python3
"""Qwen secret resolution without depending on a file holding the key.

Order (requested by the human 2026-10-20):
  1. the ``QWEN_API_KEY`` environment variable;
  2. text box on the pairing page: POST /qwen-key to the local http.server
     -> stays ONLY in this process' memory (never disk, never log);
  3. OPTIONAL DISCOURAGED LEGACY: ``[qwen] key_file`` from the config (the
     local gitignored file), only if it exists.

None of this writes or logs the secret: every string passes through the
`KeyHolder` scrubber, which masks ALL keys seen (env, form, file).
"""

from __future__ import annotations

import os

from game_session import AgentError

MASK = "***MASKED***"
ENV_NAME = "QWEN_API_KEY"


class KeyHolder:
    """Keeps the key(s) in memory and doubles as the logger's scrubber.

    - `set(key)`: arrival via the form (releases `wait`).
    - `register(key)`: arrival via env/legacy file (masking only).
    - `scrub(text)`: callable compatible with GameLogger(scrub=...)."""

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
            raise ValueError("empty key")
        self._remember(key)
        self._key = key
        self._event.set()
        return key

    def register(self, key):
        """Notes an already-known key (env/file) so scrub masks it from the
        first log line, without arming the form wait."""
        self._remember(str(key).strip() if key else None)

    def wait(self, timeout):
        """Waits `timeout` s for a key pasted in the form; None if it never arrives."""
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
    """Applies the order env > form (memory) > legacy file.

    Returns (key, source). Raises AgentError with an actionable message when
    there is none (no infinite wait: `form_timeout` + `--key-timeout`).
    """
    # 1) environment variable
    env_val = (os.environ.get(env_name) or "").strip()
    if env_val:
        holder.register(env_val)
        log("qwen key: taken from the environment variable %s" % env_name)
        return env_val, "env"

    # 2) pairing page key box (process memory only)
    if allow_form:
        log("qwen key: no %s set - paste it in the pairing page key box "
            "(%ds of slack; it stays ONLY in this process' memory) or use "
            "the legacy key_file" % (env_name, form_timeout))
        key = holder.wait(form_timeout)
        if key:
            log("qwen key: received from the form (memory; never disk, never log)")
            return key, "form"
    else:
        key = holder.get()  # stub mode has no local server: only env/file count
        if key:
            return key, "form"

    # 3) optional legacy: local file (discouraged: a secret on disk)
    if key_file and os.path.exists(key_file):
        import qwen_client
        try:
            key = qwen_client.load_key(key_file)
        except qwen_client.QwenError as e:
            raise AgentError("qwen key: %s" % holder.scrub(e))
        holder.register(key)
        log("WARNING (discouraged): key read from the legacy file %s. Rotate "
            "the key and use %s or the pairing page key box."
            % (key_file, env_name))
        return key, "file"

    raise AgentError(
        "no Qwen API key: paste it in the pairing page key box (it stays in "
        "memory only), or set the environment variable %s (Windows: setx %s "
        "\"sk-...\" and reopen the console; see README). The legacy "
        "[qwen] key_file still works as a discouraged last resort. Nothing "
        "was saved to disk."
        % (env_name, env_name))
