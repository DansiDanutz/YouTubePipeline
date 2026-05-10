"""Wan 2.2 image-to-video client — drives Alibaba Wan 2.2 TI2V 5B via the
ComfyUI-WanVideoWrapper custom node. Production img→video lane for the OSS
pipeline.

Local model: Wan2.2-TI2V-5B-Q4_K_M.gguf (3.2GB) — sized for Mac M-series.
Cloud alternative: ltx-2.3-22b-dev-fp8 (higher quality, no local RAM).

Required local model files:
  models/diffusion_models/Wan2.2-TI2V-5B-Q4_K_M.gguf  (✅ downloaded)
  models/vae/wan2.2_vae.safetensors                   (needs download)
  models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors  (needs download)

When the supporting models aren't present, this client reports unavailable
and the chain falls through to comfy-cloud (which has them pre-loaded) or
local-ffmpeg procedural motion.

Architecture: Cloud first (if opt-in), then local Wan, then ffmpeg procedural.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

COMFYUI_HOST = os.environ.get('COMFYUI_HOST', 'http://127.0.0.1:8000')

WAN_UNET = os.environ.get('WAN_UNET', 'Wan2.2-TI2V-5B-Q4_K_M.gguf')
WAN_VAE  = os.environ.get('WAN_VAE',  'wan2.2_vae.safetensors')
WAN_CLIP = os.environ.get('WAN_CLIP', 'umt5_xxl_fp8_e4m3fn_scaled.safetensors')


def _comfyui_reachable(timeout_s: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(f'{COMFYUI_HOST}/system_stats', timeout=timeout_s)
        return True
    except Exception:
        return False


def _model_dir(subdir: str) -> Path:
    return Path(os.environ.get('COMFYUI_MODEL_BASE',
        '/Users/davidai/Documents/ComfyUI/models')) / subdir


def _local_assets_ready() -> dict[str, bool]:
    return {
        'unet': (_model_dir('diffusion_models') / WAN_UNET).exists(),
        'vae':  (_model_dir('vae') / WAN_VAE).exists(),
        'clip': (_model_dir('text_encoders') / WAN_CLIP).exists(),
    }


def is_configured() -> bool:
    """Wan local needs ComfyUI :8000 + all 3 model files (unet + vae + text encoder)."""
    if not _comfyui_reachable():
        return False
    return all(_local_assets_ready().values())


def status() -> dict:
    assets = _local_assets_ready()
    ok = is_configured()
    missing = [k for k, v in assets.items() if not v]
    note = ''
    if not _comfyui_reachable():
        note = 'requires ComfyUI :8000'
    elif missing:
        note = f'missing model files: {", ".join(missing)} (route via comfy-cloud instead)'
    return {
        'available':   ok,
        'host':        COMFYUI_HOST,
        'unet':        WAN_UNET,
        'vae':         WAN_VAE,
        'clip':        WAN_CLIP,
        'assets':      assets,
        'note':        note,
    }


def _build_t2v_workflow(prompt: str, negative: str, width: int, height: int,
                        length: int, steps: int, cfg: float, seed: int,
                        sampler: str, scheduler: str) -> dict:
    """Text-to-video workflow for Wan 2.2 TI2V 5B (GGUF unet + Wan VAE + UMT5 text encoder)."""
    return {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': WAN_UNET}},
        '2': {'class_type': 'CLIPLoader',     'inputs': {'clip_name': WAN_CLIP, 'type': 'wan', 'device': 'default'}},
        '3': {'class_type': 'VAELoader',      'inputs': {'vae_name': WAN_VAE}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': prompt}},
        '5': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': negative}},
        '6': {'class_type': 'EmptyHunyuanLatentVideo',
              'inputs': {'width': width, 'height': height, 'length': length, 'batch_size': 1}},
        '7': {'class_type': 'KSampler',
              'inputs': {'model': ['1', 0], 'positive': ['4', 0], 'negative': ['5', 0],
                         'latent_image': ['6', 0], 'seed': seed, 'steps': steps, 'cfg': cfg,
                         'sampler_name': sampler, 'scheduler': scheduler, 'denoise': 1.0}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['7', 0], 'vae': ['3', 0]}},
        '9': {'class_type': 'SaveAnimatedWEBP',
              'inputs': {'images': ['8', 0], 'filename_prefix': 'zmarty_wan',
                         'fps': 16, 'lossless': False, 'quality': 80}},
    }


def _submit(workflow: dict) -> str:
    body = json.dumps({'prompt': workflow}).encode('utf-8')
    req = urllib.request.Request(f'{COMFYUI_HOST}/prompt', data=body,
                                  headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode('utf-8'))
    pid = resp.get('prompt_id', '')
    if not pid:
        raise RuntimeError(f'Wan submit returned no prompt_id: {resp}')
    return pid


def _poll(prompt_id: str, timeout_s: int = 1800, interval_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f'{COMFYUI_HOST}/history/{prompt_id}', timeout=10) as r:
                hist = json.loads(r.read().decode('utf-8'))
            if prompt_id in hist:
                return hist[prompt_id]
        except Exception:
            pass
        time.sleep(interval_s)
    raise TimeoutError(f'Wan job {prompt_id} did not complete in {timeout_s}s')


def text_to_video(prompt: str, output_path: str | Path,
                  width: int = 720, height: int = 480, length: int = 33,
                  steps: int = 20, cfg: float = 5.0, seed: int = 42,
                  sampler: str = 'uni_pc', scheduler: str = 'simple',
                  negative: str = 'low quality, blurry, distorted, watermark, text',
                  timeout_s: int = 1800) -> dict:
    """Generate a video clip from a text prompt via local Wan 2.2 TI2V 5B.

    Returns: {'ok': bool, 'path': str | None, 'duration_s': float}
    Defaults to 720x480 @ 33 frames = ~2s at 16fps. Adjust length for longer clips.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        if not is_configured():
            return {'ok': False, 'error': 'wan local not configured', 'duration_s': 0.0,
                    'status': status()}
        workflow = _build_t2v_workflow(prompt, negative, width, height, length,
                                        steps, cfg, seed, sampler, scheduler)
        pid = _submit(workflow)
        record = _poll(pid, timeout_s=timeout_s)
        outputs = record.get('outputs', {}).get('9', {})
        # SaveAnimatedWEBP emits 'images' or 'animated_images' key
        for key in ('images', 'animated_images', 'gifs'):
            items = outputs.get(key, [])
            if items:
                fname = items[0].get('filename')
                subfolder = items[0].get('subfolder', '')
                view_url = (f'{COMFYUI_HOST}/view?filename={urllib.parse.quote(fname)}'
                            f'&type=output&subfolder={urllib.parse.quote(subfolder)}')
                with urllib.request.urlopen(view_url, timeout=120) as r:
                    out.write_bytes(r.read())
                return {
                    'ok': True,
                    'path': str(out),
                    'prompt_id': pid,
                    'duration_s': round(time.monotonic() - t0, 2),
                    'comfy_filename': fname,
                    'length_frames': length,
                }
        return {'ok': False, 'error': f'no video in outputs: {outputs}',
                'prompt_id': pid, 'duration_s': round(time.monotonic() - t0, 2)}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'duration_s': round(time.monotonic() - t0, 2)}
