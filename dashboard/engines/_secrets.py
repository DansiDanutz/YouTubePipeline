#!/usr/bin/env python3.13
"""Canonical secret resolver for the Zmarty pipeline.

Resolution order (first hit wins):
  1. os.environ — already-loaded shell env (highest priority)
  2. macOS Keychain (when on Darwin) — `security find-generic-password -s <name> -w`
  3. ~/.openclaw/fleet.env — file-based (placeholders auto-rejected)

Placeholders like ${VAR}, ${VAR:-}, ${VAR:-default} (quoted or unquoted) are
treated as not-set — they're shell template literals that only resolve at
source-time of ~/.openclaude/load-secrets.sh.

Usage:
    from engines._secrets import resolve, has_key
    key = resolve('OPENROUTER_API_KEY')        # tries env → keychain → fleet.env
    if has_key('FAL_API_KEY'): ...

The mapping `KEYCHAIN_MAP` mirrors ~/.openclaude/load-secrets.sh exactly so
both paths populate the same env vars. To add a new service:
  1. Add the keychain entry on Mac via:   security add-generic-password -s <name> -w
  2. Add (env_var → keychain_name) here
  3. Pipeline picks it up automatically — no engine changes needed
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

# Match ${VAR}, ${VAR:-}, ${VAR:-default} — quoted or unquoted
_PLACEHOLDER = re.compile(
    r'^["\']?\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}["\']?$'
)


# Mapping pipeline env vars → macOS Keychain service names.
# Source: ~/.openclaude/load-secrets.sh on mac-studio.
# Keep in sync if load-secrets.sh changes.
KEYCHAIN_MAP: dict[str, str] = {
    # OpenClaude / model routing
    'ZAI_AUTH_TOKEN':                 'openclaude.zai',
    'OPENROUTER_API_KEY':             'openclaude.openrouter',
    'MOONSHOT_API_KEY':               'openclaude.moonshot',
    'KIMI_MOONSHOT_API_KEY':          'kimi.moonshot',
    'EXA_API_KEY':                    'openclaude.exa',
    'FIRECRAWL_API_KEY':              'openclaude.firecrawl',
    # OpenClaw / production providers
    'OPENAI_API_KEY':                 'openclaw.openai',
    'GEMINI_API_KEY':                 'openclaw.gemini',
    'NVIDIA_API_KEY':                 'openclaw.nvidia',
    'NOTION_API_KEY':                 'openclaw.notion',
    'DASHSCOPE_API_KEY':              'openclaw.dashscope',     # Alibaba Qwen
    'PERPLEXITY_API_KEY':             'openclaw.perplexity',
    'BRIGHTDATA_API_TOKEN':           'openclaw.brightdata',
    'BRAVE_API_KEY':                  'openclaw.brave',
    'FAL_API_KEY':                    'openclaw.fal',
    'FAL_KEY':                        'openclaw.fal',
    'PAPERCLIP_API_KEY':              'openclaw.paperclip',
    'COMFY_CLOUD_API_KEY':            'COMFY_CLOUD_API_KEY',     # Comfy Cloud opt-in lane
    'OPENCLAW_GATEWAY_TOKEN':         'openclaw.gateway_token',
    'OPENCLAW_GATEWAY_REMOTE_TOKEN':  'openclaw.gateway_remote_token',
    'TELEGRAM_WEBHOOK_SECRET_TOKEN':  'openclaw.telegram_webhook_secret',
    # Hermes
    'ANTHROPIC_API_KEY':              'hermes.anthropic_api_key',
    'HERMES_ANTHROPIC_API_KEY':       'hermes.anthropic_api_key',
    'GLM_API_KEY':                    'hermes.glm_api_key',
    'OLLAMA_API_KEY':                 'hermes.ollama_cloud',
    'TAVILY_API_KEY':                 'hermes.tavily',
    'PARALLEL_API_KEY':               'hermes.parallel',
    'GITHUB_TOKEN':                   'hermes.github_token',
    'DISCORD_BOT_TOKEN':              'hermes.discord_bot_token',
    # Larrybrain
    'LARRYBRAIN_API_KEY':             'larrybrain.api_key',
    # Telegram bots (DansLab + Hermes + fleet)
    'DLS_TELEGRAM_BOT_TOKEN':         'openclaw.telegram.dls_telegram_bot_token',
    'HERMES_TELEGRAM_BOT_TOKEN':      'openclaw.telegram.hermes_telegram_bot_token',
    'DAVID_NERVIX_BOT_TOKEN':         'openclaw.telegram.david_nervix_bot_token',
    'NERVIX_AGORA_BOT_TOKEN':         'openclaw.telegram.nervix_agora_bot_token',
    # Fleet of 4 specialists + Hermes
    'FLEET_REDIS_PASSWORD':           'fleet.redis_password',
    'FLEET_DAVID_BOT_TOKEN':          'fleet.telegram.david',
    'FLEET_DEXTER_BOT_TOKEN':         'fleet.telegram.dexter',
    'FLEET_MEMO_BOT_TOKEN':           'fleet.telegram.memo',
    'FLEET_SIENNA_BOT_TOKEN':         'fleet.telegram.sienna',
    'FLEET_NANO_BOT_TOKEN':           'fleet.telegram.nano',
    'FLEET_HERMES_BOT_TOKEN':         'fleet.telegram.hermes',
}


def _is_placeholder(v: str) -> bool:
    """True for ${VAR}, ${VAR:-}, ${VAR:-default} (quoted or unquoted)."""
    return bool(_PLACEHOLDER.match((v or '').strip()))


# Cache the macOS Keychain availability so we don't probe `security` repeatedly
_KEYCHAIN_AVAILABLE: Optional[bool] = None
_SECURITY_BIN: Optional[str] = None


def _keychain_ok() -> bool:
    global _KEYCHAIN_AVAILABLE, _SECURITY_BIN
    if _KEYCHAIN_AVAILABLE is not None:
        return _KEYCHAIN_AVAILABLE
    if platform.system() != 'Darwin':
        _KEYCHAIN_AVAILABLE = False
        return False
    _SECURITY_BIN = shutil.which('security')
    _KEYCHAIN_AVAILABLE = bool(_SECURITY_BIN)
    return _KEYCHAIN_AVAILABLE


def _from_keychain(name: str, timeout_s: float = 5.0) -> str:
    """Read a keychain entry by service name. Returns '' on any failure."""
    if not _keychain_ok():
        return ''
    try:
        out = subprocess.run(
            [_SECURITY_BIN, 'find-generic-password', '-s', name, '-w'],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ''


def _from_windows_env(name: str) -> str:
    """Read persisted Windows User/Machine environment values that the current
    process may not have inherited. Never logs values.
    """
    if sys.platform != 'win32':
        return ''
    try:
        import winreg
        targets = (
            (winreg.HKEY_CURRENT_USER, 'Environment'),
            (winreg.HKEY_LOCAL_MACHINE,
             r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
        )
        for hive, subkey in targets:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, name)
                    val = str(val or '')
                    if val and not _is_placeholder(val):
                        return val
            except OSError:
                continue
    except Exception:
        return ''
    return ''


# Cache the parsed fleet.env so we don't re-read on every resolve()
_FLEET_CACHE: Optional[dict[str, str]] = None
_FLEET_MTIME: float = -1.0


def _load_fleet_env() -> dict[str, str]:
    """Parse ~/.openclaw/fleet.env, skipping placeholder values.

    Cached by mtime — re-parses only when the file changes.
    Public so legacy engines can replace their per-module copies.
    """
    global _FLEET_CACHE, _FLEET_MTIME
    if not FLEET_ENV.exists():
        _FLEET_CACHE = {}
        _FLEET_MTIME = -1.0
        return _FLEET_CACHE
    mtime = FLEET_ENV.stat().st_mtime
    if _FLEET_CACHE is not None and mtime == _FLEET_MTIME:
        return _FLEET_CACHE
    out: dict[str, str] = {}
    try:
        for raw in FLEET_ENV.read_text(encoding='utf-8', errors='replace').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[len('export '):]
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            v = v.strip()
            if _is_placeholder(v):
                continue
            out[k.strip()] = v.strip('"').strip("'")
    except Exception:
        pass
    _FLEET_CACHE = out
    _FLEET_MTIME = mtime
    return out


def resolve(*names: str, default: str = '') -> str:
    """Look up the first non-placeholder value across all known sources.

    Tries each `name` in turn; returns first hit. Resolution order per name:
      1. os.environ (rejecting placeholders)
      2. Windows persisted env (User/Machine)
      3. macOS Keychain (using KEYCHAIN_MAP)
      4. fleet.env (file-based, placeholders skipped)

    Returns `default` if no source has a real value.
    """
    # Pass 1 — env (rejecting placeholders since some shells leak templates)
    for n in names:
        v = os.environ.get(n) or ''
        if v and not _is_placeholder(v):
            return v
    # Pass 2 — keychain (only if Darwin + security on PATH)
    if _keychain_ok():
        for n in names:
            kc = KEYCHAIN_MAP.get(n)
            if not kc:
                continue
            v = _from_keychain(kc)
            if v and not _is_placeholder(v):
                return v
    # Pass 3 — fleet.env file
    fleet = _load_fleet_env()
    for n in names:
        v = fleet.get(n) or ''
        if v and not _is_placeholder(v):
            return v
    return default


def has_key(*names: str) -> bool:
    """True iff any of the given env-var aliases resolves to a real value."""
    return bool(resolve(*names))


def status_snapshot(env_vars: list[str]) -> dict[str, dict]:
    """For each requested var, return where (if anywhere) it resolves from.

    Useful for the providers status board: shows {env, keychain, fleet, none}
    plus a length so the dashboard can verify "real key, not placeholder".

    NEVER returns the value itself.
    """
    out: dict[str, dict] = {}
    fleet = _load_fleet_env()
    for n in env_vars:
        ev = os.environ.get(n) or ''
        if ev and not _is_placeholder(ev):
            out[n] = {'configured': True, 'source': 'env', 'len': len(ev)}
            continue
        wev = _from_windows_env(n)
        if wev and not _is_placeholder(wev):
            out[n] = {'configured': True, 'source': 'windows-env', 'len': len(wev)}
            continue
        if _keychain_ok():
            kc = KEYCHAIN_MAP.get(n)
            if kc:
                v = _from_keychain(kc)
                if v and not _is_placeholder(v):
                    out[n] = {'configured': True, 'source': 'keychain',
                              'keychain_name': kc, 'len': len(v)}
                    continue
        fv = fleet.get(n) or ''
        if fv and not _is_placeholder(fv):
            out[n] = {'configured': True, 'source': 'fleet.env', 'len': len(fv)}
            continue
        out[n] = {'configured': False, 'source': None}
    return out


def hydrate_environ(names: list[str] | None = None) -> dict[str, str]:
    """Populate os.environ for any var that resolves but isn't already set.

    Call once at server boot so subprocess children + module reads via
    os.environ.get() see the resolved values without each engine repeating the
    keychain probe.

    Returns a dict of {name: source} for the keys that were hydrated this call.
    """
    targets = names or list(KEYCHAIN_MAP.keys())
    hydrated: dict[str, str] = {}
    for n in targets:
        if os.environ.get(n) and not _is_placeholder(os.environ[n]):
            continue
        v = resolve(n)
        if v:
            os.environ[n] = v
            # Find which source resolved it (for the report)
            if _keychain_ok() and KEYCHAIN_MAP.get(n) and _from_keychain(KEYCHAIN_MAP[n]) == v:
                hydrated[n] = 'keychain'
            elif _load_fleet_env().get(n) == v:
                hydrated[n] = 'fleet.env'
            else:
                hydrated[n] = 'unknown'
    return hydrated


# Backward-compat alias for engines that imported the duplicated _key()
def key(*names: str) -> str:
    """Alias for resolve(). Mirrors the per-engine `_key()` helper."""
    return resolve(*names)


if __name__ == '__main__':
    # Manual smoke test — does NOT print values, only sources + lengths
    import json
    test_keys = [
        'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'PERPLEXITY_API_KEY',
        'ELEVENLABS_API_KEY', 'BRIGHTDATA_API_TOKEN', 'GLM_API_KEY',
        'TAVILY_API_KEY', 'EXA_API_KEY', 'FIRECRAWL_API_KEY',
        'GEMINI_API_KEY', 'DASHSCOPE_API_KEY', 'NVIDIA_API_KEY',
        'HERMES_ANTHROPIC_API_KEY', 'OPENCLAW_GATEWAY_TOKEN',
        'FAL_API_KEY', 'HIGGSFIELD_API_KEY',
    ]
    print(f'Platform: {platform.system()} ({platform.machine()})')
    print(f'Keychain available: {_keychain_ok()}')
    print(f'fleet.env keys (real, post-placeholder-filter): {len(_load_fleet_env())}')
    print()
    snap = status_snapshot(test_keys)
    for k in test_keys:
        s = snap[k]
        if s['configured']:
            print(f'  ✓ {k:32s} from {s["source"]} (len={s["len"]})')
        else:
            print(f'  ✗ {k:32s} not configured')
