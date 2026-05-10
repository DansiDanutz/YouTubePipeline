"""Comfy Cloud client — opt-in remote rendering lane for production-quality
workflows that exceed local Mac RAM (Flux dev, Wan 2.2 14B I2V, LTX-Video, etc).

API discovered + verified 2026-05-11:
  Base URL : https://cloud.comfy.org
  Auth     : X-API-Key header (NOT Bearer)
  Submit   : POST /api/prompt        body={"prompt": <comfyui workflow JSON>}
  Status   : GET  /api/jobs/<id>     -> {execution_status: {...}, outputs: {...}}
  Download : GET  /api/view?filename=<hash>  -> 302 to signed GCS URL (follow with -L)

OSS-only mandate compliance:
  Cloud is OPT-IN ONLY. Two gates required to fire:
    1. ZMARTY_USE_COMFY_CLOUD=1 in env (or 'cloud' explicit in IMAGE_GEN_PROVIDERS)
    2. COMFY_CLOUD_API_KEY resolvable from Keychain / fleet.env

  Default chain stays local — no silent paid spend.

  Cloud catalog (61 checkpoints) includes models we cannot run locally:
    - flux1-dev-fp8.safetensors            (production-quality, OOMs on 36GB Mac)
    - sd3.5_large_fp8_scaled.safetensors   (best SD3.5)
    - ltx-2-19b-distilled-fp8.safetensors  (state-of-art video gen)
    - ltx-2.3-22b-dev-fp8.safetensors      (newer LTX)
    - SUPIR-v0Q_fp16.safetensors           (best photorealistic upscaler)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any

CLOUD_BASE = os.environ.get('COMFY_CLOUD_BASE', 'https://cloud.comfy.org')

# Default checkpoint when caller doesn't specify — production Flux dev that OOMs locally.
CLOUD_DEFAULT_CHECKPOINT = os.environ.get('COMFY_CLOUD_DEFAULT_CHECKPOINT', 'flux1-dev-fp8.safetensors')

# Cloud-specific quality model preferences (pick whichever is appropriate for the step):
CLOUD_PREFERRED_IMAGE = ['flux1-dev-fp8.safetensors', 'sd3.5_large_fp8_scaled.safetensors']
CLOUD_PREFERRED_VIDEO = ['ltx-2.3-22b-dev-fp8.safetensors', 'ltx-2-19b-distilled-fp8.safetensors']
CLOUD_PREFERRED_UPSCALE = ['SUPIR-v0Q_fp16.safetensors']


def _key() -> str:
    """Resolve API key via _secrets (Keychain → fleet.env → env)."""
    try:
        from . import _secrets
        return _secrets.resolve('COMFY_CLOUD_API_KEY')
    except Exception:
        return os.environ.get('COMFY_CLOUD_API_KEY', '')


def is_configured() -> bool:
    """True only when the user has explicitly opted in AND credentials exist."""
    explicit_opt_in = os.environ.get('ZMARTY_USE_COMFY_CLOUD', '').strip().lower() in ('1', 'true', 'yes')
    return bool(explicit_opt_in and _key())


def status() -> dict:
    """For /api/providers/status — surfaces opt-in state separately from key presence
    so the dashboard can communicate the gating logic clearly."""
    has_key = bool(_key())
    opted_in = os.environ.get('ZMARTY_USE_COMFY_CLOUD', '').strip().lower() in ('1', 'true', 'yes')
    note = ''
    if not has_key:
        note = 'set COMFY_CLOUD_API_KEY (Keychain or fleet.env)'
    elif not opted_in:
        note = 'opt-in required: ZMARTY_USE_COMFY_CLOUD=1'
    return {
        'available':   has_key and opted_in,
        'has_key':     has_key,
        'opted_in':    opted_in,
        'base':        CLOUD_BASE,
        'note':        note,
    }


def _request(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    """Authenticated JSON request to Comfy Cloud. Raises on failure."""
    api_key = _key()
    if not api_key:
        raise RuntimeError('COMFY_CLOUD_API_KEY not configured')
    url = f'{CLOUD_BASE}{path}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'X-API-Key':    api_key,
        'Content-Type': 'application/json',
        'Accept':       'application/json',
        'User-Agent':   'zmarty-video-pipeline/1.0 comfy-cloud-client',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', errors='replace')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'comfy-cloud {method} {path} -> HTTP {e.code}: {e.read().decode("utf-8", errors="replace")[:300]}')
    except Exception as e:
        raise RuntimeError(f'comfy-cloud {method} {path} -> {e}')


def submit_workflow(workflow: dict) -> str:
    """Submit a ComfyUI API-format workflow. Returns prompt_id."""
    resp = _request('POST', '/api/prompt', {'prompt': workflow})
    pid = resp.get('prompt_id', '')
    errors = resp.get('node_errors') or {}
    if not pid:
        raise RuntimeError(f'comfy-cloud submit returned no prompt_id; errors={errors}; resp={resp}')
    return pid


def poll_job(prompt_id: str, timeout_s: int = 600, interval_s: float = 3.0) -> dict:
    """Block until job completes (success or failure). Returns full job record."""
    deadline = time.monotonic() + timeout_s
    last_status = ''
    while time.monotonic() < deadline:
        record = _request('GET', f'/api/jobs/{prompt_id}', timeout=15)
        status_str = (record.get('execution_status') or {}).get('status_str') or record.get('status') or ''
        if status_str in ('success', 'completed', 'done'):
            return record
        if status_str in ('error', 'failed'):
            raise RuntimeError(f'comfy-cloud job {prompt_id} failed: {record.get("execution_status")}')
        last_status = status_str
        time.sleep(interval_s)
    raise TimeoutError(f'comfy-cloud job {prompt_id} did not complete in {timeout_s}s (last status: {last_status})')


def download_output(filename_hash: str, dest_path: str | Path) -> str:
    """Resolve the signed GCS URL via /api/view?filename=<hash> (302 redirect)
    and stream the file to disk. Returns the local path."""
    api_key = _key()
    if not api_key:
        raise RuntimeError('COMFY_CLOUD_API_KEY not configured')
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    view_url = f'{CLOUD_BASE}/api/view?filename={urllib.parse.quote(filename_hash)}'
    req = urllib.request.Request(view_url, headers={'X-API-Key': api_key})
    # urllib follows 302 redirects by default for GET. The signed GCS URL
    # itself is unauthenticated (X-Goog-Signature in querystring).
    with urllib.request.urlopen(req, timeout=120) as r:
        with open(dest, 'wb') as f:
            while True:
                chunk = r.read(64 * 1024)
                if not chunk: break
                f.write(chunk)
    return str(dest)


def render_image(prompt_text: str, out_path: str | Path,
                 checkpoint: str = CLOUD_DEFAULT_CHECKPOINT,
                 width: int = 1024, height: int = 1024,
                 steps: int = 20, cfg: float = 1.0, seed: int = 42,
                 negative: str = '',
                 timeout_s: int = 300) -> dict:
    """One-shot helper: prompt -> Cloud Flux render -> local PNG.

    Returns: {'ok': bool, 'path': str | None, 'prompt_id': str, 'duration_s': float}
    """
    workflow = _build_flux_workflow(checkpoint, prompt_text, negative, width, height, steps, cfg, seed)
    t0 = time.monotonic()
    try:
        pid = submit_workflow(workflow)
        record = poll_job(pid, timeout_s=timeout_s)
        outputs = record.get('outputs', {})
        # Find the SaveImage output (node id "7" in our default workflow, but locate dynamically)
        for node_id, node_out in outputs.items():
            for img in node_out.get('images', []):
                fhash = img.get('filename')
                if fhash:
                    download_output(fhash, out_path)
                    return {
                        'ok': True,
                        'path': str(out_path),
                        'prompt_id': pid,
                        'duration_s': round(time.monotonic() - t0, 2),
                        'cloud_filename': fhash,
                        'checkpoint': checkpoint,
                    }
        return {'ok': False, 'error': 'no images in output', 'prompt_id': pid, 'duration_s': round(time.monotonic() - t0, 2)}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'duration_s': round(time.monotonic() - t0, 2)}


def _build_flux_workflow(checkpoint: str, prompt: str, negative: str,
                          width: int, height: int, steps: int, cfg: float, seed: int) -> dict:
    """Minimal Flux/SD3 workflow that works with both flux1-dev-fp8 and flux1-schnell-fp8.
    For Flux, cfg=1.0 is correct; for non-Flux SD checkpoints caller can override."""
    return {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': checkpoint}},
        '2': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['1', 1], 'text': prompt}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['1', 1], 'text': negative}},
        '5': {'class_type': 'KSampler', 'inputs': {
            'model': ['1', 0], 'positive': ['3', 0], 'negative': ['4', 0],
            'latent_image': ['2', 0], 'seed': seed, 'steps': steps, 'cfg': cfg,
            'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0,
        }},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['1', 2]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'zmarty_cloud'}},
    }


# ---------------------------------------------------------------------------
# Provider chain integration: this is the function step_image_gen.py calls
# when 'comfy-cloud' is in IMAGE_GEN_PROVIDERS.
# ---------------------------------------------------------------------------

def fetch_comfy_cloud(prompt: str, out_path: str, width: int = 1024, height: int = 1024,
                      seed: int = 42, **kwargs) -> dict:
    """Provider-chain entry point. Matches the signature of fetch_comfyui /
    fetch_higgsfield / etc. in step_image_gen.py."""
    return render_image(
        prompt_text=prompt,
        out_path=out_path,
        width=width,
        height=height,
        seed=seed,
        checkpoint=kwargs.get('checkpoint', CLOUD_DEFAULT_CHECKPOINT),
        steps=kwargs.get('steps', 20),
        cfg=kwargs.get('cfg', 1.0),
        negative=kwargs.get('negative', ''),
        timeout_s=kwargs.get('timeout_s', 300),
    )
