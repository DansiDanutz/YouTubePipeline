#!/usr/bin/env python3.13
"""Higgsfield REST API client — primary image + video provider for the pipeline.

Docs: https://docs.higgsfield.ai/how-to/introduction
Dashboard for keys: https://cloud.higgsfield.ai

Auth: Authorization: Key <api_key>:<api_key_secret>
Base URL: https://platform.higgsfield.ai

Async pattern:
  1. POST /{model_id} -> {status: queued, request_id, status_url, cancel_url}
  2. GET  /requests/{request_id}/status (poll until status in {completed, failed, nsfw})
  3. completed -> {images:[{url}], video:{url}}

Set HIGGSFIELD_API_KEY + HIGGSFIELD_API_KEY_SECRET in env or ~/.openclaw/fleet.env.
Also accepts a single combined HIGGSFIELD_AUTH="key:secret" if preferred.

This module replaces the per-provider scaffolds (Pollinations, Siegfried, Seedance)
as the *primary* image+video path when configured. Pollinations remains as a
zero-config fallback.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

BASE_URL = os.environ.get('HIGGSFIELD_BASE_URL', 'https://platform.higgsfield.ai').rstrip('/')

DEFAULT_T2I_MODEL = os.environ.get('HIGGSFIELD_DEFAULT_T2I',  'higgsfield-ai/soul/standard')
DEFAULT_I2V_MODEL = os.environ.get('HIGGSFIELD_DEFAULT_I2V',  'higgsfield-ai/dop')
POLL_INTERVAL_S   = float(os.environ.get('HIGGSFIELD_POLL_S', '3'))


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


def _resolve_auth() -> tuple[str, str] | None:
    """Returns (key, secret) or None if not configured.

    Accepts either:
      - HIGGSFIELD_API_KEY + HIGGSFIELD_API_KEY_SECRET (preferred), or
      - HIGGSFIELD_AUTH="key:secret"
    """
    combined = _key('HIGGSFIELD_AUTH')
    if combined and ':' in combined:
        k, s = combined.split(':', 1)
        return k.strip(), s.strip()
    k = _key('HIGGSFIELD_API_KEY')
    s = _key('HIGGSFIELD_API_KEY_SECRET', 'HIGGSFIELD_API_SECRET')
    if k and s:
        return k, s
    return None


def is_configured() -> bool:
    return _resolve_auth() is not None


def status() -> dict:
    """Snapshot for the dashboard provider bar."""
    auth = _resolve_auth()
    return {
        'configured':   auth is not None,
        'base_url':     BASE_URL,
        'dashboard':    'https://cloud.higgsfield.ai',
        'docs':         'https://docs.higgsfield.ai',
        'default_t2i':  DEFAULT_T2I_MODEL,
        'default_i2v':  DEFAULT_I2V_MODEL,
        'note':         '' if auth else 'set HIGGSFIELD_API_KEY + HIGGSFIELD_API_KEY_SECRET',
    }


def _auth_header() -> dict[str, str]:
    auth = _resolve_auth()
    if not auth:
        raise RuntimeError('Higgsfield not configured: set HIGGSFIELD_API_KEY + HIGGSFIELD_API_KEY_SECRET')
    return {'Authorization': f'Key {auth[0]}:{auth[1]}'}


def submit(model_id: str, payload: dict, timeout: int = 30) -> dict:
    """POST /{model_id}. Returns {status, request_id, status_url, cancel_url}."""
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = _auth_header()
    headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})
    req = urllib.request.Request(f'{BASE_URL}/{model_id.lstrip("/")}', data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body_txt = (e.read() or b'').decode('utf-8', errors='replace')[:600]
        raise RuntimeError(f'submit HTTP {e.code}: {body_txt}') from e


def get_status(request_id: str, timeout: int = 30) -> dict:
    headers = _auth_header()
    req = urllib.request.Request(f'{BASE_URL}/requests/{request_id}/status', headers=headers, method='GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def cancel(request_id: str, timeout: int = 15) -> bool:
    headers = _auth_header()
    req = urllib.request.Request(f'{BASE_URL}/requests/{request_id}/cancel', headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 202
    except Exception:
        return False


def _wait_until_done(request_id: str, max_wait_s: int = 240) -> dict:
    """Poll status until terminal. Returns the final status response."""
    deadline = time.time() + max_wait_s
    last = None
    while time.time() < deadline:
        try:
            last = get_status(request_id)
        except Exception as e:
            time.sleep(POLL_INTERVAL_S)
            continue
        st = last.get('status', '')
        if st in ('completed', 'failed', 'nsfw'):
            return last
        time.sleep(POLL_INTERVAL_S)
    return last or {'status': 'timeout', 'request_id': request_id}


def _download_to(url: str, out_path: str, timeout: int = 120) -> int:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    Path(out_path).write_bytes(data)
    return len(data)


def text_to_image(prompt: str, out_path: str,
                  model_id: str = '',
                  aspect_ratio: str = '16:9',
                  resolution: str = '1080p',
                  extra_params: dict | None = None,
                  max_wait_s: int = 240) -> dict:
    """Generate an image and save to disk.

    Returns {ok, path, request_id, model, elapsed, bytes} or {ok:False, error, ...}.
    """
    if not is_configured():
        return {'ok': False, 'error': 'Higgsfield not configured', 'skipped': True}
    t0 = time.time()
    model = model_id or DEFAULT_T2I_MODEL
    payload = {'prompt': prompt[:2000], 'aspect_ratio': aspect_ratio, 'resolution': resolution}
    if extra_params:
        payload.update({k: v for k, v in extra_params.items() if k not in payload})
    try:
        submit_resp = submit(model, payload)
    except Exception as e:
        return {'ok': False, 'error': f'submit failed: {e}', 'elapsed': round(time.time() - t0, 1)}
    request_id = submit_resp.get('request_id')
    if not request_id:
        return {'ok': False, 'error': f'no request_id in submit response: {submit_resp}'}
    final = _wait_until_done(request_id, max_wait_s=max_wait_s)
    if final.get('status') != 'completed':
        return {
            'ok':         False,
            'error':      f'terminal status: {final.get("status")}',
            'request_id': request_id,
            'elapsed':    round(time.time() - t0, 1),
            'response':   final,
        }
    images = final.get('images') or []
    url = (images[0] or {}).get('url') if images else ''
    if not url:
        return {'ok': False, 'error': 'no image URL in completed response', 'response': final}
    n = _download_to(url, out_path)
    return {
        'ok':         True,
        'path':       out_path,
        'request_id': request_id,
        'model':      model,
        'image_url':  url,
        'bytes':      n,
        'elapsed':    round(time.time() - t0, 1),
    }


def image_to_video(image_url_or_path: str, prompt: str, out_path: str,
                   model_id: str = '',
                   duration_s: float = 5.0,
                   extra_params: dict | None = None,
                   max_wait_s: int = 360) -> dict:
    """Generate a video clip from an input image. The image may be an https URL
    or a local path (will be uploaded as base64 in `image_base64`)."""
    if not is_configured():
        return {'ok': False, 'error': 'Higgsfield not configured', 'skipped': True}
    t0 = time.time()
    model = model_id or DEFAULT_I2V_MODEL
    payload: dict = {'prompt': prompt[:1500], 'duration_s': duration_s}
    if (image_url_or_path or '').startswith('http'):
        payload['image_url'] = image_url_or_path
    elif image_url_or_path and Path(image_url_or_path).exists():
        import base64
        payload['image_base64'] = base64.b64encode(Path(image_url_or_path).read_bytes()).decode('ascii')
    else:
        return {'ok': False, 'error': f'image_url_or_path not a URL or existing file: {image_url_or_path}'}
    if extra_params:
        payload.update({k: v for k, v in extra_params.items() if k not in payload})
    try:
        submit_resp = submit(model, payload)
    except Exception as e:
        return {'ok': False, 'error': f'submit failed: {e}', 'elapsed': round(time.time() - t0, 1)}
    request_id = submit_resp.get('request_id')
    final = _wait_until_done(request_id, max_wait_s=max_wait_s)
    if final.get('status') != 'completed':
        return {
            'ok':         False,
            'error':      f'terminal status: {final.get("status")}',
            'request_id': request_id,
            'elapsed':    round(time.time() - t0, 1),
            'response':   final,
        }
    video = final.get('video') or {}
    url = video.get('url') or ''
    if not url:
        return {'ok': False, 'error': 'no video URL in completed response', 'response': final}
    n = _download_to(url, out_path, timeout=240)
    return {
        'ok':         True,
        'path':       out_path,
        'request_id': request_id,
        'model':      model,
        'video_url':  url,
        'bytes':      n,
        'elapsed':    round(time.time() - t0, 1),
    }
