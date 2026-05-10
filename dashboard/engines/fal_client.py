#!/usr/bin/env python3.13
"""fal.ai REST client (queue API).

Provides text-to-image and image-to-video generation through fal.ai's hosted
model catalogue (Flux, Seedance, Kling, Veo, etc.). Uses stdlib HTTP only — no
`fal-client` pip dep needed.

Configuration (env vars or ~/.openclaw/fleet.env):
  - FAL_API_KEY        primary auth key (also accepted: FAL_KEY)
  - FAL_T2I_MODEL      default 'fal-ai/flux/dev'
  - FAL_I2V_MODEL      default 'fal-ai/seedance/v1/pro/image-to-video'

If unconfigured, every function returns a graceful 'skipped' response so the
pipeline degrades to other providers in the chain.

API shape (queue mode):
  1. POST https://queue.fal.run/{model_id}     -> {request_id, status_url, response_url}
  2. GET  {status_url}                          -> {status, ...}
  3. GET  {response_url} when COMPLETED          -> {images: [...]} or {video: {url}}
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

QUEUE_BASE = 'https://queue.fal.run'

DEFAULT_T2I_MODEL = 'fal-ai/flux/dev'
# Seedance 2.0 is the current premium I2V on FAL (verified live in catalog
# 2026-05-05). The old `fal-ai/seedance/v1/pro/image-to-video` returns 404.
# Override via FAL_I2V_MODEL env. Alternates available in this catalog:
#   bytedance/seedance-2.0/reference-to-video
#   fal-ai/bytedance/seedance/v1/pro/image-to-video   (Seedance 1.0 pro)
#   fal-ai/kling-video/v3/pro/image-to-video           (Kling v3)
#   fal-ai/wan-i2v                                     (Wan)
DEFAULT_I2V_MODEL = 'bytedance/seedance-2.0/image-to-video'


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
        'key':       _key('FAL_API_KEY', 'FAL_KEY'),
        't2i_model': _key('FAL_T2I_MODEL') or DEFAULT_T2I_MODEL,
        'i2v_model': _key('FAL_I2V_MODEL') or DEFAULT_I2V_MODEL,
    }


def is_configured() -> bool:
    return bool(_config()['key'])


def status() -> dict:
    cfg = _config()
    return {
        'configured': is_configured(),
        't2i_model':  cfg['t2i_model'],
        'i2v_model':  cfg['i2v_model'],
    }


def _auth_headers() -> dict:
    cfg = _config()
    return {
        'Authorization': f'Key {cfg["key"]}',
        'Content-Type':  'application/json',
        'Accept':        'application/json',
    }


def _post_json(url: str, body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _wait_for_completion(status_url: str, response_url: str,
                         max_wait_s: int = 360, poll_interval: float = 2.0) -> dict:
    """Poll status_url until COMPLETED or FAILED. Returns the result envelope."""
    deadline = time.time() + max_wait_s
    last_status = ''
    while time.time() < deadline:
        try:
            stat = _get_json(status_url, timeout=15)
        except Exception:
            time.sleep(poll_interval)
            continue
        last_status = (stat.get('status') or '').upper()
        if last_status == 'COMPLETED':
            try:
                return {'status': 'COMPLETED', 'result': _get_json(response_url, timeout=60)}
            except Exception as e:
                return {'status': 'COMPLETED', 'error': f'fetch result: {e}'}
        if last_status in ('FAILED', 'CANCELED', 'CANCELLED'):
            return {'status': last_status, 'detail': stat}
        time.sleep(poll_interval)
    return {'status': last_status or 'TIMEOUT'}


def _download_to(url: str, out_path: str, timeout: int = 240) -> int:
    """Download a (typically pre-signed) URL to disk. Returns bytes written."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = r.read()
    Path(out_path).write_bytes(payload)
    return len(payload)


def _file_to_data_uri(path: str) -> str:
    p = Path(path)
    mime, _ = mimetypes.guess_type(p.name)
    if not mime:
        mime = 'image/jpeg' if p.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
    b64 = base64.b64encode(p.read_bytes()).decode('ascii')
    return f'data:{mime};base64,{b64}'


# ───────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────

def text_to_image(prompt: str, out_path: str,
                  width: int = 1920, height: int = 1080,
                  model_id: str | None = None,
                  num_inference_steps: int = 28,
                  guidance_scale: float = 3.5,
                  max_wait_s: int = 240) -> dict:
    """Generate an image and save to out_path.

    Returns {ok, path, model, elapsed} or {ok: False, skipped|error}.
    """
    cfg = _config()
    if not cfg['key']:
        return {'ok': False, 'skipped': True, 'reason': 'FAL_API_KEY not set'}
    model = model_id or cfg['t2i_model']
    body = {
        'prompt': prompt[:4000],
        'image_size': {'width': width, 'height': height},
        'num_inference_steps': num_inference_steps,
        'guidance_scale': guidance_scale,
        'enable_safety_checker': False,
    }
    t0 = time.time()
    try:
        submit = _post_json(f'{QUEUE_BASE}/{model}', body, timeout=30)
    except Exception as e:
        return {'ok': False, 'error': f'submit: {type(e).__name__}: {e}',
                'elapsed': round(time.time() - t0, 1)}
    status_url = submit.get('status_url') or ''
    response_url = submit.get('response_url') or ''
    request_id = submit.get('request_id') or ''
    if not (status_url and response_url):
        return {'ok': False, 'error': 'fal submit returned no status_url/response_url',
                'response': submit}
    final = _wait_for_completion(status_url, response_url, max_wait_s=max_wait_s)
    if final.get('status') != 'COMPLETED':
        return {'ok': False, 'error': f'terminal status: {final.get("status")}',
                'request_id': request_id, 'elapsed': round(time.time() - t0, 1)}
    result = final.get('result') or {}
    images = result.get('images') or []
    url = (images[0] or {}).get('url') if images else ''
    if not url:
        return {'ok': False, 'error': 'no image URL in completed response',
                'response': result}
    try:
        n = _download_to(url, out_path)
    except Exception as e:
        return {'ok': False, 'error': f'download: {e}'}
    return {'ok': True, 'path': out_path, 'model': model, 'image_url': url,
            'bytes': n, 'request_id': request_id,
            'elapsed': round(time.time() - t0, 1)}


def img2vid(image_path: str, prompt: str, out_path: str,
            duration_s: float = 5.0,
            resolution: str = '720p',
            model_id: str | None = None,
            max_wait_s: int = 360) -> dict:
    """Generate a short motion clip from a static image.

    image_path may be a local file or an https URL.
    Returns {ok, path, model, elapsed} or {ok: False, skipped|error}.
    """
    cfg = _config()
    if not cfg['key']:
        return {'ok': False, 'skipped': True, 'reason': 'FAL_API_KEY not set'}
    model = model_id or cfg['i2v_model']
    if (image_path or '').startswith('http'):
        image_url = image_path
    elif image_path and Path(image_path).exists():
        image_url = _file_to_data_uri(image_path)
    else:
        return {'ok': False, 'error': f'image_path not a URL or existing file: {image_path}'}
    body = {
        'prompt': prompt[:1500],
        'image_url': image_url,
        'duration': str(int(duration_s)) if duration_s else '5',
        'resolution': resolution,
    }
    t0 = time.time()
    try:
        submit = _post_json(f'{QUEUE_BASE}/{model}', body, timeout=30)
    except Exception as e:
        return {'ok': False, 'error': f'submit: {type(e).__name__}: {e}',
                'elapsed': round(time.time() - t0, 1)}
    status_url = submit.get('status_url') or ''
    response_url = submit.get('response_url') or ''
    request_id = submit.get('request_id') or ''
    if not (status_url and response_url):
        return {'ok': False, 'error': 'fal submit returned no status_url/response_url',
                'response': submit}
    final = _wait_for_completion(status_url, response_url, max_wait_s=max_wait_s)
    if final.get('status') != 'COMPLETED':
        return {'ok': False, 'error': f'terminal status: {final.get("status")}',
                'request_id': request_id, 'elapsed': round(time.time() - t0, 1)}
    result = final.get('result') or {}
    video = result.get('video') or {}
    url = video.get('url') or ''
    if not url:
        return {'ok': False, 'error': 'no video URL in completed response',
                'response': result}
    try:
        n = _download_to(url, out_path, timeout=300)
    except Exception as e:
        return {'ok': False, 'error': f'download: {e}'}
    return {'ok': True, 'path': out_path, 'model': model, 'video_url': url,
            'bytes': n, 'request_id': request_id,
            'elapsed': round(time.time() - t0, 1)}
