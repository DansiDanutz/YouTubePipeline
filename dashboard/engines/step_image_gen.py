#!/usr/bin/env python3.13
"""Step Image-Gen — per-scene AI image generation for the YT pipeline.

For each beat in the manifest, build a story-tuned prompt and generate a
hero image via Pollinations.ai (FLUX, free, no-auth) with OpenAI DALL-E 3
as quality fallback. Each image is 1920×1080 (cropped from 16:9 generation),
saved to <work>/assets/scenes/scene_N.jpg.

Design philosophy (huashu-design):
  - Images are FULL-BLEED hero — text overlays sit on top.
  - Editorial dark — every prompt requests dark background, single accent color.
  - No people-faces unless protagonist named — abstract symbols / objects /
    architecture / data viz / atmospheric — never "AI-stock-photo of a businessman".
  - Each scene's prompt embeds the protagonist + topic so visuals are
    story-relevant, not generic decoration.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/"
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"

DEFAULT_W = 1920
DEFAULT_H = 1080

# Style anchors applied to EVERY prompt for visual cohesion across scenes
STYLE_PREFIX = (
    "editorial cinematic dark composition, deep black backdrop with subtle gold "
    "or amber accent highlights, sparse and intentional, documentary feel, "
    "shallow depth of field, soft volumetric lighting, film grain, "
    "no text, no logos, no UI mockups, "
)
STYLE_SUFFIX = (
    ", 16:9 widescreen, 1920x1080, no people unless specified, no AI-stock cliches, "
    "no purple gradients, no chromatic aberration, no blurry rendering"
)


# ───────────────────────────────────────────────────────────────────────
# Prompt builders per beat kind — each creates a STORY-RELEVANT image
# ───────────────────────────────────────────────────────────────────────

def _safe(s: str, n: int = 200) -> str:
    return (s or "")[:n].replace('"', '').replace('\n', ' ').strip()


def prompt_for_beat(beat: dict, topic_summary: str = "") -> str:
    """Build an image prompt for a beat. Each kind gets its own visual treatment."""
    data = beat.get("data", {}) or {}
    kind = beat.get("kind") or data.get("kind", "hook")
    narration = _safe(beat.get("narration", ""), 180)
    topic = _safe(topic_summary, 120)

    if kind == "hook":
        # An iconic, attention-grabbing hero image that makes you pause
        eyebrow = _safe(data.get("eyebrow", ""))
        label = _safe(data.get("label", ""))
        subj = f"{label} {narration}"
        return (STYLE_PREFIX +
                f"hero opening shot for a documentary about {topic}. "
                f"Visual metaphor: {subj}. Dramatic centered composition with "
                f"glowing element, strong negative space around the subject. "
                f"Atmospheric. The single hero element pops against deep darkness. "
                f"Inspired by Apple keynote opening, Anthropic research papers, "
                f"and Netflix tech documentary cinematography." +
                STYLE_SUFFIX)

    elif kind == "setup":
        name = _safe(data.get("name", ""), 50)
        subtitle = _safe(data.get("subtitle", ""), 60)
        # Abstract environment that suggests the protagonist's world (NOT a face)
        return (STYLE_PREFIX +
                f"abstract atmospheric scene representing the world of {name}, "
                f"{subtitle}. Setting that hints at {topic}: a developer's "
                f"workspace / a server room / chip wafers / code on a CRT "
                f"depending on context. Empty room, warm desk lamp, "
                f"single monitor glow. NO faces, NO figures — just the "
                f"environment as a character. Hopper-like cinematic loneliness." +
                STYLE_SUFFIX)

    elif kind == "conflict":
        headline = _safe(data.get("headline", ""), 80)
        return (STYLE_PREFIX +
                f"tension visualization for: {headline}. Topic context: {topic}. "
                f"Show the obstacle/problem as visual metaphor — broken glass / "
                f"a wall / mountain of paper / tangled cables / red warning haze / "
                f"scattered debris — whichever fits the story. Charged composition, "
                f"contrast between order and disorder. The mood is heavy, the air "
                f"is thick. Red or amber accent on the obstacle." +
                STYLE_SUFFIX)

    elif kind == "breakthrough":
        headline = _safe(data.get("headline", ""), 80)
        return (STYLE_PREFIX +
                f"breakthrough moment visualization for: {headline}. Topic: {topic}. "
                f"Visual: a single beam of light cutting through darkness, or "
                f"a key turning, or first dawn light on architecture, or a chip "
                f"glowing at the moment of completion. The shift from problem to "
                f"solution. Hopeful but earned — not Hallmark — Kubrickian. "
                f"Gold or amber accent dominates this frame." +
                STYLE_SUFFIX)

    elif kind == "resolution":
        headline = _safe(data.get("headline", ""), 80)
        bigStat = _safe(data.get("bigStat", ""), 30)
        return (STYLE_PREFIX +
                f"epic wide-shot resolution visual for: {headline}. Stat anchor: {bigStat}. "
                f"Topic: {topic}. Show the AFTERMATH — the new world that was created. "
                f"Aerial city of lights / global server map glow / vast factory "
                f"floor / fleet of devices — whichever fits the topic. Sense of "
                f"scale and impact. Wide cinemascope feel. Cooler color tone "
                f"than the breakthrough, blue-green dawn light." +
                STYLE_SUFFIX)

    elif kind == "cta":
        url = _safe(data.get("url", ""), 80)
        return (STYLE_PREFIX +
                f"closing minimal hero shot — clean and inviting. Topic: {topic}. "
                f"Visual: a doorway opening / a path forward / an inviting "
                f"glowing object centered. Negative space dominates. The viewer "
                f"feels invited to act. Single warm gold accent on the focal "
                f"element. Quiet. Confident." +
                STYLE_SUFFIX)

    # Fallback
    return STYLE_PREFIX + f"abstract editorial visual for {topic}: {narration}" + STYLE_SUFFIX


# ───────────────────────────────────────────────────────────────────────
# Image fetchers
# ───────────────────────────────────────────────────────────────────────

def fetch_pollinations(prompt: str, out_path: str, width: int = DEFAULT_W,
                       height: int = DEFAULT_H, seed: int | None = None,
                       timeout: int = 90, max_retries: int = 3) -> bool:
    """Fetch an image from Pollinations.ai (FLUX). Retry with backoff on 429/5xx."""
    encoded = urllib.parse.quote(prompt)
    seed = seed if seed is not None else int(time.time()) % 1_000_000
    url = (f"{POLLINATIONS_BASE}{encoded}?"
           f"width={width}&height={height}&seed={seed}&model=flux&nologo=true&enhance=false")
    backoff = 4
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZmartyVideoBot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 5000 or not (data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n'):
                # Bad payload — retry
                time.sleep(backoff); backoff *= 2; continue
            with open(out_path, 'wb') as f:
                f.write(data)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                if attempt < max_retries - 1:
                    time.sleep(backoff); backoff *= 2; continue
            print(f"    pollinations HTTP {e.code} (attempt {attempt+1}/{max_retries})")
            return False
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(backoff); backoff *= 2; continue
            print(f"    pollinations fetch failed: {e}")
            return False
    return False


def fetch_openai_dalle(prompt: str, out_path: str,
                       size: str = "1792x1024", timeout: int = 60) -> bool:
    """OpenAI DALL-E 3 as quality fallback. Costs ~$0.04 per image."""
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key:
        return False
    body = json.dumps({
        "model": "dall-e-3",
        "prompt": prompt[:4000],
        "n": 1,
        "size": size,
        "quality": "standard",
    }).encode('utf-8')
    try:
        req = urllib.request.Request(
            OPENAI_IMAGES_URL, data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        img_url = data["data"][0]["url"]
        # Download
        with urllib.request.urlopen(img_url, timeout=30) as ir:
            img_bytes = ir.read()
        with open(out_path, 'wb') as f:
            f.write(img_bytes)
        return True
    except Exception as e:
        print(f"    OpenAI DALL-E failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Quality-first multi-provider image generation:
#   1. OpenAI gpt-image-1 ("Image 2") — highest quality stills when configured
#   2. Higgsfield — cinematic/stylized stills when configured
#   3. fal.ai — reliable cloud fallback for many scenes
#   4. ComfyUI — local workflow only when reachable and model is production-good
#   5. Siegfried MCP — custom HTTP endpoint, configurable
#   6. Pollinations.ai — draft/emergency placeholder fallback
#   7. OpenAI DALL-E 3 — legacy paid fallback
# Providers can be reordered or restricted via the IMAGE_GEN_PROVIDERS env var
# (comma-separated list of provider names from the registry below).
# ---------------------------------------------------------------------------

COMFYUI_HOST = os.environ.get('COMFYUI_HOST', 'http://127.0.0.1:8188')
COMFYUI_WORKFLOW_PATH = os.environ.get(
    'COMFYUI_WORKFLOW',
    str(Path(__file__).resolve().parents[2] / 'comfyui-workflows' / 'flux-base-frames.json'),
)
SIEGFRIED_URL = os.environ.get('SIEGFRIED_URL', '')   # e.g. http://127.0.0.1:7000/generate
SIEGFRIED_KEY = os.environ.get('SIEGFRIED_API_KEY', '')


def _comfyui_reachable(timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(f'{COMFYUI_HOST}/system_stats', timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _comfyui_inject_prompt(workflow: dict, prompt: str) -> dict:
    """Walk the ComfyUI workflow JSON and replace the first text prompt input."""
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = node.get('class_type') or ''
        # CLIP/Flux text-encode nodes carry the prompt in inputs.text
        if cls in ('CLIPTextEncode', 'CLIPTextEncodeSDXL', 'CLIPTextEncodeFlux',
                   'T5TextEncode', 'FluxGuidance'):
            inputs = node.get('inputs', {})
            if 'text' in inputs and isinstance(inputs['text'], str):
                inputs['text'] = prompt
                return workflow
    return workflow


def fetch_comfyui(prompt: str, out_path: str, width: int = DEFAULT_W,
                  height: int = DEFAULT_H, timeout: int = 240) -> bool:
    """Submit a prompt to local ComfyUI, poll for completion, save the output."""
    if not _comfyui_reachable():
        return False
    try:
        workflow_path = Path(COMFYUI_WORKFLOW_PATH)
        if not workflow_path.exists():
            return False
        workflow = json.loads(workflow_path.read_text(encoding='utf-8'))
        workflow = _comfyui_inject_prompt(workflow, prompt[:1500])

        body = json.dumps({'prompt': workflow}).encode('utf-8')
        req = urllib.request.Request(
            f'{COMFYUI_HOST}/prompt', data=body,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            submit = json.loads(r.read())
        prompt_id = submit.get('prompt_id')
        if not prompt_id:
            return False

        # Poll history until the job appears with outputs
        deadline = time.time() + timeout
        outputs = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'{COMFYUI_HOST}/history/{prompt_id}', timeout=10) as r:
                    hist = json.loads(r.read())
                if prompt_id in hist and hist[prompt_id].get('outputs'):
                    outputs = hist[prompt_id]['outputs']
                    break
            except Exception:
                pass
            time.sleep(2)
        if not outputs:
            return False

        # Find first SaveImage output
        for node_id, node_out in outputs.items():
            for img in node_out.get('images', []) or []:
                fname = img.get('filename')
                subfolder = img.get('subfolder') or ''
                folder_type = img.get('type') or 'output'
                if not fname:
                    continue
                view_url = (f'{COMFYUI_HOST}/view?filename={urllib.parse.quote(fname)}'
                            f'&subfolder={urllib.parse.quote(subfolder)}'
                            f'&type={urllib.parse.quote(folder_type)}')
                with urllib.request.urlopen(view_url, timeout=30) as ir:
                    img_bytes = ir.read()
                with open(out_path, 'wb') as f:
                    f.write(img_bytes)
                return True
        return False
    except Exception as e:
        print(f'    comfyui failed: {e}')
        return False


def fetch_openai_image2(prompt: str, out_path: str,
                        size: str = "1536x1024", timeout: int = 90) -> bool:
    """OpenAI gpt-image-1 (the model formerly known as 'Image 2').

    Newer than DALL-E 3, returns base64-encoded PNG in data[0].b64_json.
    Costs ~$0.04 per 1024x1024 standard image.
    """
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key:
        return False
    body = json.dumps({
        "model": "gpt-image-1",
        "prompt": prompt[:4000],
        "n": 1,
        "size": size,
    }).encode('utf-8')
    try:
        req = urllib.request.Request(
            OPENAI_IMAGES_URL, data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        item = (data.get('data') or [{}])[0]
        # gpt-image-1 returns b64_json by default; older models return url
        if item.get('b64_json'):
            import base64
            with open(out_path, 'wb') as f:
                f.write(base64.b64decode(item['b64_json']))
            return True
        if item.get('url'):
            with urllib.request.urlopen(item['url'], timeout=30) as ir:
                with open(out_path, 'wb') as f:
                    f.write(ir.read())
            return True
        return False
    except Exception as e:
        print(f'    openai gpt-image-1 failed: {e}')
        return False


def fetch_siegfried(prompt: str, out_path: str, timeout: int = 120) -> bool:
    """Siegfried MCP image generation — custom HTTP endpoint.

    Set SIEGFRIED_URL (e.g. http://127.0.0.1:7000/generate) and optionally
    SIEGFRIED_API_KEY. Endpoint is expected to accept POST {prompt, width,
    height} and return either raw image bytes or {b64} / {url}.
    """
    if not SIEGFRIED_URL:
        return False
    body = json.dumps({
        'prompt': prompt[:2000],
        'width':  DEFAULT_W,
        'height': DEFAULT_H,
    }).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if SIEGFRIED_KEY:
        headers['Authorization'] = f'Bearer {SIEGFRIED_KEY}'
    try:
        req = urllib.request.Request(SIEGFRIED_URL, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = (r.headers.get('Content-Type') or '').lower()
            payload = r.read()
        # Direct image bytes
        if 'image/' in ct:
            with open(out_path, 'wb') as f:
                f.write(payload)
            return True
        # JSON envelope
        try:
            data = json.loads(payload)
        except Exception:
            return False
        if data.get('b64'):
            import base64
            with open(out_path, 'wb') as f:
                f.write(base64.b64decode(data['b64']))
            return True
        if data.get('url'):
            with urllib.request.urlopen(data['url'], timeout=30) as ir:
                with open(out_path, 'wb') as f:
                    f.write(ir.read())
            return True
        return False
    except Exception as e:
        print(f'    siegfried failed: {e}')
        return False


def fetch_higgsfield(prompt: str, out_path: str, width: int = DEFAULT_W,
                     height: int = DEFAULT_H, timeout: int = 240) -> bool:
    """Higgsfield REST API — primary image provider. Cloud-hosted Flux 2,
    GPT Image 2, Nano Banana 2, Soul, Seedream, etc."""
    try:
        from .higgsfield_client import is_configured, text_to_image
    except Exception:
        return False
    if not is_configured():
        return False
    # 16:9 by default if width/height match common landscape ratios
    aspect = '16:9' if abs(width / max(height, 1) - 16 / 9) < 0.05 else '1:1'
    res = '1080p' if max(width, height) >= 1500 else '720p'
    result = text_to_image(prompt, out_path, aspect_ratio=aspect, resolution=res)
    return bool(result.get('ok'))


def fetch_fal(prompt: str, out_path: str, width: int = DEFAULT_W,
              height: int = DEFAULT_H, timeout: int = 240) -> bool:
    """fal.ai REST queue API — Flux/Seedream/Nano-Banana/etc cloud generation."""
    try:
        from . import fal_client as _fal
    except Exception:
        return False
    if not _fal.is_configured():
        return False
    result = _fal.text_to_image(prompt, out_path, width=width, height=height,
                                max_wait_s=timeout)
    return bool(result.get('ok'))


def _fetch_comfy_cloud(prompt, out_path, **kwargs):
    """Lazy wrapper — only imports comfy_cloud_client when actually invoked."""
    try:
        from .comfy_cloud_client import fetch_comfy_cloud
        return fetch_comfy_cloud(prompt, out_path, **kwargs)
    except Exception as e:
        return {'ok': False, 'error': f'comfy-cloud unavailable: {e}'}


PROVIDER_REGISTRY = {
    'higgsfield':    fetch_higgsfield,
    'comfyui':       fetch_comfyui,
    'comfy-cloud':   _fetch_comfy_cloud,           # opt-in remote OSS lane
    'fal':           fetch_fal,
    'gpt-image-1':   fetch_openai_image2,
    'image2':        fetch_openai_image2,         # alias
    'siegfried':     fetch_siegfried,
    'pollinations':  fetch_pollinations,
    'dalle-3':       fetch_openai_dalle,
}

# OSS-only default. ComfyUI (Flux/SDXL local) is the single image generator.
# Paid lanes (gpt-image-1, higgsfield, fal, pollinations, dalle-3) intentionally
# excluded — pipeline must fail loudly if local ComfyUI is unreachable rather
# than silently incurring API costs. Override with IMAGE_GEN_PROVIDERS only for
# explicit, opt-in cost-bearing runs.
DEFAULT_PROVIDER_CHAIN = ['comfyui']


def _provider_chain() -> list[str]:
    raw = os.environ.get('IMAGE_GEN_PROVIDERS', '')
    if raw.strip():
        chain = [p.strip().lower() for p in raw.split(',') if p.strip()]
        return [p for p in chain if p in PROVIDER_REGISTRY] or DEFAULT_PROVIDER_CHAIN
    # Default chain. Auto-prepend 'comfy-cloud' when the user has explicitly
    # opted in (ZMARTY_USE_COMFY_CLOUD=1) AND has a key — this routes the
    # production lane through Comfy Cloud (Flux dev, LTX-Video, etc.) while
    # keeping local ComfyUI as the fallback. Two gates keep paid spend explicit.
    chain = list(DEFAULT_PROVIDER_CHAIN)
    try:
        from . import comfy_cloud_client as _cc
        if _cc.is_configured():
            chain = ['comfy-cloud'] + [p for p in chain if p != 'comfy-cloud']
    except Exception:
        pass
    return chain


def _have_real_openai_key() -> bool:
    """OpenAI Image 2 / DALL-E 3 only work with a real OpenAI key. Mac-studio's
    setup uses OPENAI_API_KEY="ollama" as a dummy to route the OpenAI SDK at a
    local Ollama; we exclude those here so the status bar doesn't lie.

    Uses _secrets.resolve() which: (a) checks os.environ first, (b) skips
    ${VAR:-} placeholder literals, (c) falls through to keychain, then
    fleet.env. Without this, the prior implementation read the placeholder
    line from fleet.env, found it non-empty, and shortcircuited away from the
    real env value — which is why gpt-image-1 was reading FALSE on Mac even
    when OPENAI_API_KEY was correctly hydrated.
    """
    try:
        from . import _secrets
        key = _secrets.resolve('OPENAI_API_KEY')
    except Exception:
        key = os.environ.get('OPENAI_API_KEY', '')
    # Real OpenAI keys: sk-..., sk-proj-..., or sk-svcacct-... — all 'sk-' prefix
    # and >20 chars. "ollama"/"dummy"/short strings are not real keys.
    return bool(key and key.startswith('sk-') and len(key) > 20)


def image_provider_status() -> dict:
    """Snapshot of which providers can run right now (for the dashboard)."""
    real_openai = _have_real_openai_key()
    local_visuals_ok = bool(shutil.which('ffmpeg') or shutil.which('ffmpeg.exe'))
    higgs_ok = False
    higgs_note = ''
    try:
        from .higgsfield_client import is_configured as _hf_ok, status as _hf_status
        higgs_ok = _hf_ok()
        hfs = _hf_status()
        higgs_note = hfs.get('note', '')
    except Exception:
        pass
    fal_ok = False
    try:
        from . import fal_client as _fal
        fal_ok = _fal.is_configured()
    except Exception:
        pass
    cloud_status = {'available': False, 'has_key': False, 'opted_in': False, 'note': '(client not loaded)'}
    try:
        from . import comfy_cloud_client as _cc
        cloud_status = _cc.status()
    except Exception:
        pass
    return {
        'higgsfield':   {'available': higgs_ok, 'note': higgs_note},
        'comfyui':      {'available': _comfyui_reachable(), 'host': COMFYUI_HOST},
        'comfy-cloud':  cloud_status,
        'fal':          {'available': fal_ok, 'note': '' if fal_ok else 'set FAL_API_KEY'},
        'gpt-image-1':  {'available': real_openai, 'note': '' if real_openai else 'needs real OpenAI key (sk-...)'},
        'siegfried':    {'available': bool(SIEGFRIED_URL), 'url': SIEGFRIED_URL or '(unset)'},
        'pollinations': {'available': True, 'note': 'public free endpoint'},
        'local-procedural': {'available': local_visuals_ok, 'note': 'Remotion/FFmpeg procedural scene fallback'},
        'dalle-3':      {'available': real_openai},
        'chain':        _provider_chain(),
    }


def video_provider_status() -> dict:
    """Snapshot of which video (img→video) providers can run right now."""
    seedance_ok = False
    higgs_ok = False
    higgs_note = ''
    fal_ok = False
    ffmpeg_ok = bool(shutil.which('ffmpeg') or shutil.which('ffmpeg.exe'))
    try:
        from . import seedance as _sd
        seedance_ok = _sd.is_configured()
    except Exception:
        pass
    try:
        from .higgsfield_client import is_configured as _hf_ok, status as _hf_status
        higgs_ok = _hf_ok()
        hfs = _hf_status()
        higgs_note = hfs.get('note', '')
    except Exception:
        pass
    try:
        from . import fal_client as _fal
        fal_ok = _fal.is_configured()
    except Exception:
        pass
    cloud_status = {'available': False, 'has_key': False, 'opted_in': False, 'note': '(client not loaded)'}
    try:
        from . import comfy_cloud_client as _cc
        cs = _cc.status()
        # Video on Cloud uses LTX-Video lineup (ltx-2-19b/22b) — same auth gates as image
        cloud_status = {**cs, 'note': cs.get('note') or 'LTX-Video 19B/22B available on Cloud'}
    except Exception:
        pass
    wan_status = {'available': False, 'note': '(client not loaded)'}
    try:
        from . import wan_client as _wan
        wan_status = _wan.status()
    except Exception:
        pass
    return {
        'comfyui':      {'available': _comfyui_reachable(), 'host': COMFYUI_HOST,
                         'note': 'requires comfyui-workflows/wan21-img2vid.json'},
        'comfy-cloud':  cloud_status,
        'wan':          wan_status,
        'seedance':     {'available': seedance_ok, 'note': '' if seedance_ok else 'set SEEDANCE_API_URL + SEEDANCE_API_KEY'},
        'higgsfield':   {'available': higgs_ok, 'note': higgs_note},
        'fal':          {'available': fal_ok, 'note': '' if fal_ok else 'set FAL_API_KEY'},
        'local-ffmpeg': {'available': ffmpeg_ok, 'note': 'local per-scene MP4 motion fallback'},
        'chain':        _video_provider_chain() + ['local-ffmpeg'],
    }


# ---------------------------------------------------------------------------
# Per-scene image→video chain (used by step7_render._generate_scene_clips).
# Defaults to quality-first cloud image-to-video, then local ComfyUI. Override
# via VIDEO_GEN_PROVIDERS for draft/local-only runs. Each provider gracefully
# reports unavailable when not configured.
# ---------------------------------------------------------------------------

DEFAULT_VIDEO_CHAIN = ['comfyui']  # OSS-only (Wan 2.1/2.2 via local ComfyUI). local-ffmpeg auto-appended in status.


def _video_provider_chain() -> list[str]:
    raw = os.environ.get('VIDEO_GEN_PROVIDERS', '')
    if raw.strip():
        chain = [p.strip().lower() for p in raw.split(',') if p.strip()]
        return chain or DEFAULT_VIDEO_CHAIN
    # Default chain. Auto-prepend OSS lanes when their gates are satisfied:
    #   1. Wan 2.2 local (when ComfyUI :8000 + all 3 model files present)
    #   2. Comfy Cloud LTX-Video (when ZMARTY_USE_COMFY_CLOUD=1 + key)
    #   3. local ComfyUI (the existing default)
    # Each provider self-checks at invocation time, so prepending is safe.
    chain = list(DEFAULT_VIDEO_CHAIN)
    try:
        from . import wan_client as _wan
        if _wan.is_configured() and 'wan' not in chain:
            chain = ['wan'] + chain
    except Exception:
        pass
    try:
        from . import comfy_cloud_client as _cc
        if _cc.is_configured() and 'comfy-cloud' not in chain:
            chain = ['comfy-cloud'] + chain
    except Exception:
        pass
    return chain


COMFYUI_I2V_WORKFLOW_PATH = os.environ.get(
    'COMFYUI_I2V_WORKFLOW',
    str(Path(__file__).resolve().parents[2] / 'comfyui-workflows' / 'wan21-img2vid.json'),
)


def _comfyui_inject_image(workflow: dict, image_path: str) -> dict:
    """Inject the input image path into the first LoadImage-family node."""
    p = Path(image_path)
    fname = p.name
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        cls = node.get('class_type') or ''
        if cls in ('LoadImage', 'LoadImageMask', 'VHS_LoadImagePath'):
            inputs = node.get('inputs', {})
            if 'image' in inputs:
                inputs['image'] = fname
                return workflow
    return workflow


def fetch_comfyui_i2v(image_path: str, prompt: str, out_path: str,
                      duration_s: float = 5.0, timeout: int = 360) -> dict:
    """Submit image+prompt to local ComfyUI Wan 2.1 workflow, save the resulting
    video. Returns {ok, path, model, elapsed} or {ok: False, skipped|error}."""
    if not _comfyui_reachable():
        return {'ok': False, 'skipped': True, 'reason': 'ComfyUI not reachable'}
    workflow_path = Path(COMFYUI_I2V_WORKFLOW_PATH)
    if not workflow_path.exists():
        return {'ok': False, 'skipped': True,
                'reason': f'workflow missing: {workflow_path}'}
    if not Path(image_path).exists():
        return {'ok': False, 'error': f'input image missing: {image_path}'}
    t0 = time.time()
    try:
        # Upload image to ComfyUI's input dir so LoadImage can find it
        try:
            import io
            boundary = '----ZmartyComfyUploader'
            img_bytes = Path(image_path).read_bytes()
            fname = Path(image_path).name
            body = io.BytesIO()
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{fname}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode())
            body.write(img_bytes)
            body.write(f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n--{boundary}--\r\n'.encode())
            up_req = urllib.request.Request(
                f'{COMFYUI_HOST}/upload/image', data=body.getvalue(),
                headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
            )
            urllib.request.urlopen(up_req, timeout=30).read()
        except Exception:
            pass  # If upload fails, fall through; workflow may already reference an image

        workflow = json.loads(workflow_path.read_text(encoding='utf-8'))
        workflow = _comfyui_inject_prompt(workflow, prompt[:1500])
        workflow = _comfyui_inject_image(workflow, image_path)

        body_json = json.dumps({'prompt': workflow}).encode('utf-8')
        req = urllib.request.Request(
            f'{COMFYUI_HOST}/prompt', data=body_json,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            submit = json.loads(r.read())
        prompt_id = submit.get('prompt_id')
        if not prompt_id:
            return {'ok': False, 'error': 'comfyui submit returned no prompt_id'}

        deadline = time.time() + timeout
        outputs = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'{COMFYUI_HOST}/history/{prompt_id}', timeout=10) as r:
                    hist = json.loads(r.read())
                if prompt_id in hist and hist[prompt_id].get('outputs'):
                    outputs = hist[prompt_id]['outputs']
                    break
            except Exception:
                pass
            time.sleep(3)
        if not outputs:
            return {'ok': False, 'error': 'comfyui timeout'}

        # First video output
        for node_out in outputs.values():
            for vid in (node_out.get('videos', []) or node_out.get('gifs', []) or []) or []:
                fname = vid.get('filename')
                if not fname:
                    continue
                subfolder = vid.get('subfolder') or ''
                folder_type = vid.get('type') or 'output'
                view_url = (f'{COMFYUI_HOST}/view?filename={urllib.parse.quote(fname)}'
                            f'&subfolder={urllib.parse.quote(subfolder)}'
                            f'&type={urllib.parse.quote(folder_type)}')
                with urllib.request.urlopen(view_url, timeout=120) as ir:
                    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(out_path).write_bytes(ir.read())
                return {'ok': True, 'path': out_path, 'model': 'comfyui-wan21',
                        'elapsed': round(time.time() - t0, 1)}
        return {'ok': False, 'error': 'no video in comfyui outputs'}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'elapsed': round(time.time() - t0, 1)}


def fetch_video_with_fallback(image_path: str, prompt: str, out_path: str,
                              duration_s: float = 5.0,
                              scene_id: int = 0) -> dict:
    """Run the video provider chain — first success wins. Returns
    {ok, path, provider, attempts, elapsed}.

    Each provider is wrapped in try/except + skipped flag, so the entire stage
    is opt-in — if no provider is configured, returns ok=False, skipped=True
    and the caller should fall back to the static hero image.
    """
    t0 = time.time()
    chain = _video_provider_chain()
    attempts: list[dict] = []
    for name in chain:
        try:
            if name == 'comfyui':
                result = fetch_comfyui_i2v(image_path, prompt, out_path, duration_s)
            elif name == 'seedance':
                from . import seedance as _sd
                result = _sd.img2vid(image_path, prompt, out_path, duration_s=duration_s)
            elif name == 'higgsfield':
                from . import higgsfield_client as _hf
                if not _hf.is_configured():
                    result = {'ok': False, 'skipped': True}
                else:
                    result = _hf.image_to_video(image_path, prompt, out_path,
                                                duration_s=duration_s)
            elif name == 'fal':
                from . import fal_client as _fal
                result = _fal.img2vid(image_path, prompt, out_path,
                                      duration_s=duration_s)
            else:
                continue
        except Exception as e:
            attempts.append({'provider': name, 'ok': False,
                             'error': f'{type(e).__name__}: {e}'})
            continue
        attempts.append({'provider': name, 'ok': bool(result.get('ok')),
                         'skipped': bool(result.get('skipped')),
                         'reason': result.get('reason') or result.get('error') or ''})
        if result.get('ok'):
            return {'scene': scene_id, 'path': out_path, 'provider': name,
                    'attempts': attempts, 'elapsed': round(time.time() - t0, 1),
                    'ok': True}
    return {'scene': scene_id, 'path': '', 'provider': 'none',
            'attempts': attempts, 'elapsed': round(time.time() - t0, 1),
            'ok': False, 'skipped': all(a.get('skipped') for a in attempts)}


def fetch_with_fallback(prompt: str, out_path: str, scene_id: int) -> dict:
    """Run the configured provider chain, returning the first success."""
    t0 = time.time()
    chain = _provider_chain()
    attempts: list[dict] = []
    for name in chain:
        fn = PROVIDER_REGISTRY.get(name)
        if not fn:
            continue
        try:
            ok = fn(prompt, out_path)
        except Exception as e:
            attempts.append({'provider': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'})
            continue
        attempts.append({'provider': name, 'ok': bool(ok)})
        if ok:
            return {
                "scene":    scene_id,
                "path":     out_path,
                "provider": name,
                "attempts": attempts,
                "elapsed":  round(time.time() - t0, 1),
                "ok":       True,
            }
    return {"scene": scene_id, "path": "", "provider": "none",
            "attempts": attempts, "elapsed": round(time.time() - t0, 1), "ok": False}


# ───────────────────────────────────────────────────────────────────────
# Public entrypoint
# ───────────────────────────────────────────────────────────────────────

def generate_scene_images(manifest: dict, work_dir: str,
                          topic_summary: str = "",
                          max_workers: int = 4) -> list[dict]:
    """For each beat in the manifest, generate a story-relevant hero image.
    Returns list of {scene, path, provider, elapsed, ok, prompt}."""
    scenes_dir = Path(work_dir) / "assets" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    beats = manifest.get("beats", [])
    if not beats:
        return []

    tasks = []
    for i, beat in enumerate(beats, start=1):
        prompt = prompt_for_beat(beat, topic_summary)
        out_path = str(scenes_dir / f"scene_{i:02d}.jpg")
        tasks.append((i, prompt, out_path))

    results: list[dict] = []
    print(f"  ▶ generating {len(tasks)} hero images via Pollinations FLUX (parallel x{max_workers})")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(fetch_with_fallback, p, op, sid) for sid, p, op in tasks]
        # Map back to source prompts
        for (sid, prompt, op), fut in zip(tasks, futs):
            r = fut.result()
            r["prompt"] = prompt
            results.append(r)
            status = "✓" if r["ok"] else "✗"
            print(f"    {status} scene_{sid:02d} ({r['provider']}, {r['elapsed']}s)")

    ok = sum(1 for r in results if r["ok"])
    print(f"  done in {time.time()-t0:.1f}s · {ok}/{len(tasks)} succeeded")
    return sorted(results, key=lambda r: r["scene"])


if __name__ == "__main__":
    # Smoke-test
    import sys
    if len(sys.argv) < 3:
        print("usage: step_image_gen.py <work_dir> <manifest.json>")
        sys.exit(1)
    work, mf = sys.argv[1], sys.argv[2]
    manifest = json.load(open(mf))
    out = generate_scene_images(manifest, work, topic_summary="smoke test")
    print(json.dumps(out, indent=2))
