#!/usr/bin/env python3.13
"""Daily auto-discovery scheduler.

Implements the spec mechanism: "1 time per day" — automatically refresh the
OSS registry (HuggingFace trending + GitHub repos + arXiv) when the on-disk
registry is older than 24 hours. Runs as a lightweight daemon thread inside
the dashboard server process — no external cron required, Windows-friendly.

Design:
  - Thread sleeps `check_every_seconds` between checks (default 1h)
  - On wake-up: read `~/.openclaw/oss_registry.json`'s `updated_at` field
  - If stale (> max_age_hours since last update), fire `run_discovery()`
  - Otherwise: skip silently
  - All work done in a daemon thread, so the server can shut down cleanly

Idempotent: starting the scheduler twice is a no-op (re-uses the existing
thread). Run-on-start fires once shortly after server boot if stale; otherwise
honours the existing on-disk timestamp.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import json

from .discovery import REGISTRY_PATH, run_discovery

DEFAULT_MAX_AGE_HOURS    = 24
DEFAULT_CHECK_EVERY_SECS = 3600   # poll once an hour
DEFAULT_BOOT_DELAY_SECS  = 30     # let the server come up before first check

_state_lock = threading.Lock()
_state: dict = {
    'thread':           None,
    'started_at':       None,
    'last_check_at':    None,
    'last_fire_at':     None,
    'last_fire_result': None,
    'fires':            0,
    'checks':           0,
    'max_age_hours':    DEFAULT_MAX_AGE_HOURS,
    'check_every_secs': DEFAULT_CHECK_EVERY_SECS,
}


def _registry_age_hours() -> float | None:
    """Hours since the registry's `updated_at`. None if registry doesn't exist
    or is unreadable."""
    if not REGISTRY_PATH.exists():
        return None
    try:
        data = json.loads(REGISTRY_PATH.read_text('utf-8'))
        ts = data.get('updated_at') or ''
        if not ts:
            return None
        # tolerate both '...Z' and '...+00:00'
        ts = ts.replace('Z', '+00:00')
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - last
        return delta.total_seconds() / 3600.0
    except Exception:
        return None


def discovery_is_stale(max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
    """True iff registry is missing OR older than max_age_hours."""
    age = _registry_age_hours()
    if age is None:
        return True
    return age >= max_age_hours


def _scheduler_loop(max_age_hours: float, check_every_secs: int,
                    boot_delay_secs: int) -> None:
    """Daemon thread body. Sleeps, checks, fires if stale, repeats forever."""
    if boot_delay_secs > 0:
        time.sleep(boot_delay_secs)
    while True:
        try:
            with _state_lock:
                _state['last_check_at'] = datetime.now(timezone.utc).isoformat()
                _state['checks'] += 1
            if discovery_is_stale(max_age_hours):
                # Fire — this can take 30-90s; not a problem for a daemon thread.
                result = run_discovery(verbose=False)
                with _state_lock:
                    _state['last_fire_at']     = datetime.now(timezone.utc).isoformat()
                    _state['last_fire_result'] = {
                        'tool_count':    result.get('tool_count'),
                        'new_this_run':  result.get('new_this_run'),
                        'elapsed':       result.get('elapsed'),
                    }
                    _state['fires'] += 1
        except Exception as e:
            with _state_lock:
                _state['last_fire_result'] = {'error': f'{type(e).__name__}: {e}'}
        time.sleep(check_every_secs)


def start_daily_discovery_thread(
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    check_every_secs: int = DEFAULT_CHECK_EVERY_SECS,
    boot_delay_secs: int = DEFAULT_BOOT_DELAY_SECS,
) -> dict:
    """Start the scheduler daemon thread. Idempotent — second call is a no-op
    while the existing thread is still alive."""
    with _state_lock:
        if _state['thread'] is not None and _state['thread'].is_alive():
            return {'started': False, 'reason': 'already running',
                    'started_at': _state['started_at']}
        t = threading.Thread(
            target=_scheduler_loop,
            args=(max_age_hours, check_every_secs, boot_delay_secs),
            name='discovery-scheduler',
            daemon=True,
        )
        _state['thread']           = t
        _state['started_at']       = datetime.now(timezone.utc).isoformat()
        _state['max_age_hours']    = max_age_hours
        _state['check_every_secs'] = check_every_secs
        t.start()
        return {'started': True, 'started_at': _state['started_at'],
                'max_age_hours': max_age_hours, 'check_every_secs': check_every_secs}


def scheduler_status() -> dict:
    """Compact snapshot for /api/scheduler/status."""
    with _state_lock:
        snap = {k: v for k, v in _state.items() if k != 'thread'}
    snap['running']           = bool(_state.get('thread') and _state['thread'].is_alive())
    snap['registry_age_hrs']  = _registry_age_hours()
    snap['stale']             = discovery_is_stale(snap.get('max_age_hours', DEFAULT_MAX_AGE_HOURS))
    return snap


def force_discovery_now() -> dict:
    """Synchronous force-run, used by the manual override endpoint."""
    started = time.time()
    try:
        result = run_discovery(verbose=False)
        with _state_lock:
            _state['last_fire_at']     = datetime.now(timezone.utc).isoformat()
            _state['last_fire_result'] = {
                'tool_count':    result.get('tool_count'),
                'new_this_run':  result.get('new_this_run'),
                'elapsed':       result.get('elapsed'),
                'forced':        True,
            }
            _state['fires'] += 1
        return {'ok': True, 'elapsed': round(time.time() - started, 2),
                'tool_count': result.get('tool_count'),
                'new_this_run': result.get('new_this_run')}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}
