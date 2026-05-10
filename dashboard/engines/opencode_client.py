#!/usr/bin/env python3.13
"""OpenCode CLI wrapper — calls `opencode run` as a subprocess and returns the
text response.

OpenCode is an LLM-driven coding agent (https://github.com/sst/opencode) that
runs locally and supports many providers. The pipeline uses it as an optional
high-quality alternative to local Ollama for creative stages (Step 2 narrative,
Step 9 final review).

If OpenCode isn't installed or fails, callers should fall back to their
existing Ollama / Perplexity path. This module is "best effort" — it never
raises, only returns {ok: False} on failure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_TIMEOUT = 240
DEFAULT_AGENT   = os.environ.get('OPENCODE_AGENT', 'general')
DEFAULT_MODEL   = os.environ.get('OPENCODE_MODEL', '')


def _opencode_path() -> str | None:
    """Locate the opencode executable. Tries PATH, then npm-global on Windows."""
    found = shutil.which('opencode')
    if found:
        return found
    # Windows npm-global fallback (PATH may not include APPDATA/npm in subprocess env)
    npm_appdata = os.environ.get('APPDATA', '')
    if npm_appdata:
        for name in ('opencode.cmd', 'opencode'):
            p = Path(npm_appdata) / 'npm' / name
            if p.exists():
                return str(p)
    # macOS/Linux npm-global fallback
    home = Path.home()
    for p in (home / '.npm-global' / 'bin' / 'opencode',
              Path('/usr/local/bin/opencode')):
        if p.exists():
            return str(p)
    return None


def is_available() -> bool:
    return _opencode_path() is not None


def run(prompt: str, timeout: int = DEFAULT_TIMEOUT,
        model: str = '', agent: str = '',
        cwd: str | None = None) -> dict:
    """Call `opencode run` headlessly. Returns
        {ok, text, session_id, tokens, cost, elapsed}
    or {ok: False, error}.
    """
    bin_path = _opencode_path()
    if not bin_path:
        return {'ok': False, 'error': 'opencode binary not found in PATH or npm-global'}

    args = [bin_path, 'run', '--format', 'json']
    if model or DEFAULT_MODEL:
        args += ['--model', model or DEFAULT_MODEL]
    if agent or DEFAULT_AGENT:
        args += ['--agent', agent or DEFAULT_AGENT]
    args.append(prompt)

    t0 = time.time()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            encoding='utf-8',
            errors='replace',
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'timeout after {timeout}s', 'elapsed': timeout}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    if proc.returncode != 0:
        return {
            'ok': False,
            'error': f'exit {proc.returncode}: {(proc.stderr or "")[:300]}',
            'elapsed': round(time.time() - t0, 1),
        }

    # Parse the streamed JSON events; concatenate all text parts.
    text_parts: list[str] = []
    session_id = ''
    tokens = {}
    cost = 0.0
    for line in (proc.stdout or '').splitlines():
        line = line.strip()
        if not line or not line.startswith('{'):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        ev_type = ev.get('type', '')
        part = ev.get('part') or {}
        if not session_id:
            session_id = ev.get('sessionID') or part.get('sessionID') or ''
        if ev_type == 'text':
            t = part.get('text') or ''
            if t:
                text_parts.append(t)
        elif ev_type == 'step_finish':
            tokens = part.get('tokens') or {}
            cost = part.get('cost') or 0.0

    text = '\n'.join(text_parts).strip()
    return {
        'ok':         bool(text),
        'text':       text,
        'session_id': session_id,
        'tokens':     tokens,
        'cost':       cost,
        'elapsed':    round(time.time() - t0, 1),
        'model':      model or DEFAULT_MODEL or '(default)',
        'agent':      agent or DEFAULT_AGENT,
    }


def status() -> dict:
    """For dashboard provider status bar."""
    path = _opencode_path()
    return {
        'available': path is not None,
        'path':      path or '(not found)',
    }
