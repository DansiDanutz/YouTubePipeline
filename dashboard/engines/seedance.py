#!/usr/bin/env python3.13
"""Seedance 2.0 video generation client (ByteDance).

Image-to-video / text-to-video generation. Used by Step 7 (render) to
optionally turn each scene's hero image into a 5-second motion clip before
Remotion compositing.

Configuration (env vars or ~/.openclaw/fleet.env):
  - SEEDANCE_API_URL     base URL for the Seedance API
  - SEEDANCE_API_KEY     bearer token
  - SEEDANCE_MODEL       'seedance-2.0' (default) or '-pro' / '-fast'

If unconfigured, every function returns a graceful 'skipped' response so the
pipeline degrades to Remotion-only rendering. Wire up by either:
  1. Setting the env vars / fleet.env entries
  2. Or replacing _api_call() with a different provider's signature
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'


def _load_fleet_env() -> dict:
    env: dict = {}
    if not FLEET_ENV.exists():
        return env
    try:
        for raw in FLEET_ENV.read_text(errors='ignore').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[len('export '):]
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


_FLEET = _load_fleet_env()


def _key(*names: str) -> str:
    for name in names:
        v = os.environ.get(name) or _FLEET.get(name) or ''
        if v:
            return v
    return ''


def _config() -> dict:
    return {
        'url':   _key('SEEDANCE_API_URL', 'SEEDANCE_URL'),
        'key':   _key('SEEDANCE_API_KEY', 'SEEDANCE_KEY'),
        'model': _key('SEEDANCE_MODEL') or 'seedance-2.0',
    }


def is_configured() -> bool:
    cfg = _config()
    return bool(cfg['url'] and cfg['key'])


def status() -> dict:
    cfg = _config()
    return {
        'configured': is_configured(),
        'url':        cfg['url'] or '(unset)',
        'model':      cfg['model'],
    }


def img2vid(image_path: str, prompt: str, out_path: str,
            duration_s: float = 5.0, timeout: int = 240) -> dict:
    """Generate a short motion clip from a static hero image.

    Returns {ok, path, model, elapsed} or {ok: False, skipped|error}.
    """
    cfg = _config()
    if not (cfg['url'] and cfg['key']):
        return {'ok': False, 'skipped': True,
                'reason': 'SEEDANCE_API_URL / SEEDANCE_API_KEY not set'}
    if not Path(image_path).exists():
        return {'ok': False, 'error': f'image not found: {image_path}'}

    t0 = time.time()
    import base64
    img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode('ascii')
    body = json.dumps({
        'model':       cfg['model'],
        'image_base64': img_b64,
        'prompt':      prompt[:1500],
        'duration_s':  duration_s,
    }).encode('utf-8')
    try:
        req = urllib.request.Request(
            f'{cfg["url"].rstrip("/")}/img2vid',
            data=body,
            headers={
                'Authorization': f'Bearer {cfg["key"]}',
                'Content-Type':  'application/json',
                'Accept':        'video/mp4',
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = (r.headers.get('Content-Type') or '').lower()
            payload = r.read()
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        if 'video/' in ct:
            Path(out_path).write_bytes(payload)
            return {'ok': True, 'path': out_path, 'model': cfg['model'],
                    'elapsed': round(time.time() - t0, 1)}
        # JSON envelope alternative
        try:
            data = json.loads(payload)
        except Exception:
            return {'ok': False, 'error': 'unrecognized response format'}
        if data.get('video_url'):
            with urllib.request.urlopen(data['video_url'], timeout=120) as ir:
                Path(out_path).write_bytes(ir.read())
            return {'ok': True, 'path': out_path, 'model': cfg['model'],
                    'elapsed': round(time.time() - t0, 1)}
        return {'ok': False, 'error': data.get('error') or 'no video in response'}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'elapsed': round(time.time() - t0, 1)}
