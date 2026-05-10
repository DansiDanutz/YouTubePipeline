"""Kokoro TTS client — drives Kokoro-82M ONNX (Apache 2.0, OSS) directly via
Python without ComfyUI. Designed as the production OSS TTS lane that replaces
VibeVoice (which has an upstream transformers class-conflict bug in the
ComfyUI custom node).

Why Kokoro over VibeVoice for production:
  - Apache 2.0 (commercial-OK for the membership product)
  - Single ONNX file (~325 MB) — no transformers, no class conflicts
  - Native Mac MPS / CPU support, no ComfyUI dependency
  - Sub-second synthesis on Apple Silicon (5x realtime+)
  - Multiple voice presets (af_heart, af_alloy, am_michael, etc.)

Install (deferred — runs when memory permits):
    pip install kokoro-onnx soundfile
    # Models auto-download on first use OR pre-fetch:
    huggingface-cli download onnx-community/Kokoro-82M-v1.0-ONNX onnx/model_q8f16.onnx --local-dir ~/.cache/kokoro
    huggingface-cli download onnx-community/Kokoro-82M-v1.0-ONNX voices-v1.0.bin --local-dir ~/.cache/kokoro

The client is import-safe even when kokoro-onnx isn't installed — is_configured()
returns False and the spec stays on the Piper fallback.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

KOKORO_MODEL_PATH = os.environ.get('KOKORO_MODEL_PATH',
    str(Path.home() / '.cache' / 'kokoro' / 'onnx' / 'model_q8f16.onnx'))
KOKORO_VOICES_PATH = os.environ.get('KOKORO_VOICES_PATH',
    str(Path.home() / '.cache' / 'kokoro' / 'voices-v1.0.bin'))
KOKORO_DEFAULT_VOICE = os.environ.get('KOKORO_DEFAULT_VOICE', 'af_heart')
KOKORO_DEFAULT_LANG = os.environ.get('KOKORO_DEFAULT_LANG', 'en-us')
KOKORO_DEFAULT_SPEED = float(os.environ.get('KOKORO_DEFAULT_SPEED', '1.0'))

# Module-level singleton — Kokoro loads its ONNX once, then synthesizes fast.
_kokoro_singleton = None


def _have_lib() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile    # noqa: F401
        return True
    except Exception:
        return False


def _have_models() -> bool:
    return Path(KOKORO_MODEL_PATH).exists() and Path(KOKORO_VOICES_PATH).exists()


def is_configured() -> bool:
    """Available when kokoro-onnx is pip-installed AND both model files exist."""
    return _have_lib() and _have_models()


def status() -> dict:
    has_lib = _have_lib()
    has_models = _have_models()
    note = ''
    if not has_lib:
        note = 'pip install kokoro-onnx soundfile'
    elif not has_models:
        note = (f'download model: huggingface-cli download '
                f'onnx-community/Kokoro-82M-v1.0-ONNX onnx/model_q8f16.onnx voices-v1.0.bin '
                f'--local-dir ~/.cache/kokoro')
    return {
        'available':   has_lib and has_models,
        'has_lib':     has_lib,
        'has_models':  has_models,
        'model':       KOKORO_MODEL_PATH,
        'voices':      KOKORO_VOICES_PATH,
        'default_voice': KOKORO_DEFAULT_VOICE,
        'note':        note,
    }


def _get_kokoro():
    """Lazy singleton — load ONNX once, reuse for subsequent synthesis."""
    global _kokoro_singleton
    if _kokoro_singleton is None:
        from kokoro_onnx import Kokoro
        _kokoro_singleton = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
    return _kokoro_singleton


def synthesize(text: str, output_wav: str | Path,
               voice: str = KOKORO_DEFAULT_VOICE,
               lang: str = KOKORO_DEFAULT_LANG,
               speed: float = KOKORO_DEFAULT_SPEED) -> dict:
    """Generate WAV from text via Kokoro-82M.

    Returns: {'ok': bool, 'path': str | None, 'duration_s': float, 'error'?: str}
    """
    out = Path(output_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        if not is_configured():
            return {'ok': False, 'error': 'kokoro not configured', 'duration_s': 0.0,
                    'status': status()}
        import soundfile as sf
        kokoro = _get_kokoro()
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
        sf.write(str(out), samples, sample_rate)
        return {
            'ok': True,
            'path': str(out),
            'duration_s': round(time.monotonic() - t0, 2),
            'voice': voice,
            'lang': lang,
            'sample_rate': sample_rate,
            'audio_seconds': round(len(samples) / sample_rate, 2),
        }
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'duration_s': round(time.monotonic() - t0, 2)}


# CLI entry: `python -m engines.kokoro_client "<text>" "<output.wav>" [voice]`
if __name__ == '__main__':
    import sys
    import json
    if len(sys.argv) < 3:
        print('usage: python -m engines.kokoro_client "<text>" "<output.wav>" [voice]', file=sys.stderr)
        sys.exit(2)
    text = sys.argv[1]
    out = sys.argv[2]
    voice = sys.argv[3] if len(sys.argv) > 3 else KOKORO_DEFAULT_VOICE
    result = synthesize(text, out, voice=voice)
    print(json.dumps(result))
    sys.exit(0 if result.get('ok') else 1)
