"""VibeVoice TTS client — drives Microsoft VibeVoice 1.5B (~5GB OSS, MIT) via
the ComfyUI VibeVoice-ComfyUI custom node API. Production TTS lane that
replaces Piper as default narration when ComfyUI :8000 is reachable.

Why VibeVoice over Piper:
  - Native voice-cloning from reference audio (Piper has no cloning)
  - Up to 4 distinct speakers in one synthesis (multi-speaker)
  - 90-min continuous generation capability
  - Apple Silicon native (MPS) — Mac-friendly
  - MIT license — commercial-OK for the membership product

Workflow we submit (single-speaker mode):
  1. VibeVoiceSingleSpeakerNode  — text + model + diffusion_steps + cfg_scale
  2. SaveAudio                    — write WAV to ComfyUI/output

Then we resolve the output filename from /api/jobs and download via /api/view.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

COMFYUI_HOST = os.environ.get('COMFYUI_HOST', 'http://127.0.0.1:8000')

# Model display names as registered by VibeVoice-ComfyUI from the local
# ComfyUI/models/vibevoice/ folder. The default 1.5B is the only one we
# downloaded; alternatives (Large, Large-Q8, Large-Q4) require separate downloads.
VIBEVOICE_DEFAULT_MODEL = os.environ.get('VIBEVOICE_DEFAULT_MODEL', 'VibeVoice-1.5B')


def _comfyui_reachable(timeout_s: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(f'{COMFYUI_HOST}/system_stats', timeout=timeout_s)
        return True
    except Exception:
        return False


def is_configured() -> bool:
    """VibeVoice runs through the local ComfyUI desktop app — only available
    when the app is up AND has the VibeVoice-ComfyUI custom node loaded AND the
    1.5B model files exist on disk."""
    if not _comfyui_reachable():
        return False
    model_dir = Path(os.environ.get('VIBEVOICE_MODEL_DIR',
        '/Users/davidai/Documents/ComfyUI/models/vibevoice')) / VIBEVOICE_DEFAULT_MODEL
    return (model_dir / 'config.json').exists()


def status() -> dict:
    has_model = is_configured()
    return {
        'available':   has_model,
        'host':        COMFYUI_HOST,
        'model':       VIBEVOICE_DEFAULT_MODEL,
        'note':        '' if has_model else 'requires ComfyUI :8000 + VibeVoice-1.5B in models/vibevoice/',
    }


def _build_workflow(text: str, model: str, diffusion_steps: int, cfg_scale: float,
                    seed: int, attention_type: str, free_memory_after: bool) -> dict:
    """Single-speaker TTS workflow. Output is a WAV via SaveAudio."""
    return {
        '1': {
            'class_type': 'VibeVoiceSingleSpeakerNode',
            'inputs': {
                'text':                       text,
                'model':                      model,
                'attention_type':             attention_type,
                'quantize_llm':               'full precision',
                'free_memory_after_generate': free_memory_after,
                'diffusion_steps':            diffusion_steps,
                'seed':                       seed,
                'cfg_scale':                  cfg_scale,
                'use_sampling':               False,
            },
        },
        '2': {
            'class_type': 'SaveAudio',
            'inputs': {
                'audio':           ['1', 0],
                'filename_prefix': 'zmarty_vibevoice',
            },
        },
    }


def _submit(workflow: dict) -> str:
    body = json.dumps({'prompt': workflow}).encode('utf-8')
    req = urllib.request.Request(f'{COMFYUI_HOST}/prompt', data=body,
                                  headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode('utf-8'))
    pid = resp.get('prompt_id', '')
    if not pid:
        raise RuntimeError(f'VibeVoice submit returned no prompt_id: {resp}')
    return pid


def _poll(prompt_id: str, timeout_s: int = 600, interval_s: float = 3.0) -> dict:
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
    raise TimeoutError(f'VibeVoice job {prompt_id} did not complete in {timeout_s}s')


def synthesize(text: str, output_wav: str | Path,
               model: str = VIBEVOICE_DEFAULT_MODEL,
               diffusion_steps: int = 20, cfg_scale: float = 1.3, seed: int = 42,
               attention_type: str = 'auto', free_memory_after: bool = True,
               timeout_s: int = 600) -> dict:
    """Generate WAV from text via local ComfyUI VibeVoice node.

    Returns: {'ok': bool, 'path': str | None, 'duration_s': float, 'error'?: str}
    """
    out = Path(output_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        if not _comfyui_reachable():
            return {'ok': False, 'error': 'ComfyUI :8000 unreachable', 'duration_s': 0.0}
        workflow = _build_workflow(text, model, diffusion_steps, cfg_scale, seed,
                                    attention_type, free_memory_after)
        pid = _submit(workflow)
        record = _poll(pid, timeout_s=timeout_s)
        # SaveAudio output is in node "2"; filename varies by ComfyUI build
        outputs = record.get('outputs', {}).get('2', {})
        for key in ('audio', 'audios', 'wavs'):
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
                    'cloud_filename': fname,
                    'model': model,
                }
        return {'ok': False, 'error': f'no audio in outputs: {outputs}',
                'prompt_id': pid, 'duration_s': round(time.monotonic() - t0, 2)}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'duration_s': round(time.monotonic() - t0, 2)}
