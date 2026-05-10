#!/usr/bin/env python3.13
"""Step 4 — Scene Production engine.

Takes Step 2's locked narration script + Step 3's locked design system and
produces a concrete SCENE MANIFEST: per-scene Remotion component blueprints,
animation timeline maps, asset specifications, and render settings.

Output maps the 6 GDS sections (Hook / Thesis / Evidence×2 / Implication / CTA)
to production-ready scene configs the renderer (Step 7) can execute.

Pipeline (5 stages, same shape as Steps 1-3):
  Stage 1 — HERMES PRE-ROUTE     (design system → production constraints)
  Stage 2 — SCENE OUTLINE        (timing architecture + scene sequencing)
  Stage 3 — DRAFT SCENE MANIFEST (per-scene Remotion blueprints + assets)
  Stage 4 — VALIDATE             (timing math, asset coverage, frame arithmetic)
  Stage 5 — FLEET REVIEW         (render feasibility / timing / assets / engagement)

Hard validators:
  • Total timeline matches the selected video length (Remotion at 30fps)
  • All computed scenes are accounted for with non-zero durations
  • Each scene has: component_type, animation_in, animation_out, asset_list
  • Asset list entries have name, type, dimensions
  • No layout-bound animation properties (width/height/top/left/margin/padding)
  • Motion properties compositor-only: transform/opacity/clip-path/filter
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL = os.environ.get('STEP4_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL = os.environ.get('STEP4_DEEP_MODEL', 'sonar-pro')

GDS_SECTIONS = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']
GDS_LABELS = {
    'hook': 'Hook', 'thesis': 'Thesis',
    'evidence_1': 'Evidence 1', 'evidence_2': 'Evidence 2',
    'implication': 'Implication', 'cta': 'CTA',
}

COMPOSITOR_FRIENDLY = {'transform', 'opacity', 'clip-path', 'filter', 'translate', 'scale', 'rotate'}
LAYOUT_BOUND = {'width', 'height', 'top', 'left', 'margin', 'padding', 'border', 'font-size'}

TARGET_SECONDS = 41
FPS = 30
TARGET_FRAMES = TARGET_SECONDS * FPS  # 1230
DEFAULT_SCENE_SECONDS = 15

VALID_COMPONENT_TYPES = {
    'TextSlide', 'SplitFrame', 'DataViz', 'FullBleedMedia',
    'OverlayCaption', 'TypographyHero', 'ChartReveal', 'CodeBlock',
    'TimelineScroll', 'GridReveal', 'Kinetic', 'StatCounter',
}


def compute_scene_targets(length_seconds: int = TARGET_SECONDS,
                          scene_seconds: int = DEFAULT_SCENE_SECONDS) -> dict:
    """Compute the actual scene plan for the selected video length.

    A 900-second / 15-minute video at 15 seconds per scene is 60 scenes.
    This must be deterministic; otherwise long-form runs silently collapse back
    to the old six-scene short explainer shape.
    """
    length_seconds = max(20, min(int(length_seconds or TARGET_SECONDS), 1800))
    scene_seconds = max(5, min(int(scene_seconds or DEFAULT_SCENE_SECONDS), 30))
    scene_count = max(1, math.ceil(length_seconds / scene_seconds))
    return {
        'length_seconds': length_seconds,
        'scene_seconds': scene_seconds,
        'scene_count': scene_count,
        'fps': FPS,
        'total_frames': int(length_seconds * FPS),
        'run_mode': 'production-long-form' if length_seconds >= 300 else ('standard' if length_seconds > 60 else 'short'),
    }


def _scene_ids(scene_count: int) -> list[str]:
    if scene_count <= len(GDS_SECTIONS):
        return GDS_SECTIONS[:scene_count]
    return [f'scene_{i:03d}' for i in range(1, scene_count + 1)]


def _split_words(text: str, chunks: int) -> list[str]:
    words = re.findall(r'\S+', text or '')
    if not words:
        return [''] * chunks
    per = max(1, math.ceil(len(words) / max(chunks, 1)))
    return [' '.join(words[i * per:(i + 1) * per]) for i in range(chunks)]


def _arc_label(index: int, total: int) -> str:
    p = index / max(total - 1, 1)
    if p < 0.08:
        return 'Cold Open'
    if p < 0.18:
        return 'Context'
    if p < 0.35:
        return 'Setup'
    if p < 0.62:
        return 'Evidence'
    if p < 0.78:
        return 'Analysis'
    if p < 0.92:
        return 'Implication'
    return 'CTA'


def deterministic_scene_outline(step2_script: str, targets: dict) -> dict:
    scene_count = int(targets['scene_count'])
    length_seconds = int(targets['length_seconds'])
    scene_seconds = int(targets['scene_seconds'])
    ids = _scene_ids(scene_count)
    chunks = _split_words(step2_script, scene_count)
    scenes = []
    cursor = 0
    for i, scene_id in enumerate(ids):
        remaining = length_seconds - (i * scene_seconds)
        dur = float(scene_seconds if i < scene_count - 1 else max(1, remaining))
        frame_start = cursor
        frame_end = frame_start + int(round(dur * FPS))
        excerpt = chunks[i] if i < len(chunks) else ''
        arc = _arc_label(i, scene_count)
        scenes.append({
            'scene_id': scene_id,
            'label': f'{arc} {i + 1}',
            'duration_seconds': dur,
            'frame_start': frame_start,
            'frame_end': frame_end,
            'narration_excerpt': excerpt[:500],
            'visual_focus': f'{arc} beat from the narration, scene {i + 1} of {scene_count}',
            'story_function': arc.lower().replace(' ', '_'),
            'editable': True,
        })
        cursor = frame_end
    return {
        'total_seconds': round(cursor / FPS, 1),
        'target_seconds': length_seconds,
        'scene_seconds': scene_seconds,
        'scene_count': scene_count,
        'total_frames': cursor,
        'fps': FPS,
        'run_mode': targets.get('run_mode', 'standard'),
        'scenes': scenes,
    }


# ---------------------------------------------------------------------------
# Env / key resolution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _ollama_headers() -> dict:
    """Build request headers; adds Bearer auth when OLLAMA_API_KEY is set."""
    h = {'Content-Type': 'application/json'}
    import os as _os
    k = _os.environ.get('OLLAMA_API_KEY', '')
    if k:
        h['Authorization'] = f'Bearer {k}'
    return h


def _call_ollama(prompt: str, model: str = LOCAL_MODEL, timeout: int = 240) -> str:
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                       'stream': False}).encode('utf-8')
    try:
        req = urllib.request.Request(
            f'{OLLAMA_HOST}/v1/chat/completions',
            data=body, headers=_ollama_headers(), method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
            return data['choices'][0]['message']['content']
    except Exception as e:
        return f'_(ollama error: {e})_'


def _call_perplexity(prompt: str, timeout: int = 60) -> str:
    key = _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY')
    if not key:
        return '_(perplexity key missing)_'
    body = json.dumps({'model': DEEP_MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                       'stream': False, 'max_tokens': 4096}).encode('utf-8')
    try:
        req = urllib.request.Request(
            'https://api.perplexity.ai/chat/completions',
            data=body,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
            return data['choices'][0]['message']['content']
    except Exception as e:
        return f'_(perplexity error: {e})_'


def _extract_json(text: str) -> dict | None:
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```\s*$', '', (text or '').strip(), flags=re.MULTILINE)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Stage 1 — Hermes pre-route
# ---------------------------------------------------------------------------

HERMES_TEMPLATE = """You are Hermes orchestrating a SCENE PRODUCTION step. The user has:
  - A locked research brief (Step 1)
  - A locked narration script (Step 2)
  - A locked design system (Step 3)

Your job is to route the production work to the right specialist and surface
constraints that the scene manifest MUST respect.

DESIGN SYSTEM SUMMARY (from Step 3):
{design_system_summary}

NARRATION SCRIPT (Step 2, drives scene timing):
{step2_script}

USER NOTES on this iteration (may be empty):
{notes}

RECENT LEARNINGS (from past Step 4 runs — what scene patterns worked, what failed):
{learnings}

Output VALID JSON ONLY:
{{
  "render_target": "<remotion | canvas2d | css-animation | mixed>",
  "complexity_tier": "<simple | medium | complex>",
  "primary_component_family": "<data-heavy | text-driven | media-rich | hybrid>",
  "frame_budget_hint": "<conservative=1150 | standard=1230 | rich=1280> (use standard by default)",
  "fleet_owner_hint": "<Dexter | Memo | Sienna | Nano>",
  "must_have_assets": ["<asset types explicitly needed: e.g. 'data chart', 'portrait photo', 'code snippet'>"],
  "risk_flags": ["<production risks: e.g. 'data viz requires real numbers', 'stock footage needed'>"],
  "stop_or_proceed": "PROCEED|STOP",
  "stop_reason": ""
}}
"""


def hermes_preroute(design_system: dict, step2_script: str = '', notes: str = '') -> dict:
    try:
        from .learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        learnings_text = '(learnings store unavailable)'

    ds_summary = {
        'visual_archetype': design_system.get('visual_archetype', ''),
        'palette': [c.get('hex') for c in (design_system.get('color_palette') or [])[:4]],
        'display_font': (design_system.get('typography') or {}).get('display_font', ''),
        'motion_principles': (design_system.get('motion') or {}).get('principles', [])[:3],
        'energy_level': design_system.get('energy_level', 'medium'),
    }

    payload = HERMES_TEMPLATE.format(
        design_system_summary=json.dumps(ds_summary, indent=2)[:800],
        step2_script=(step2_script or '(Step 2 script not locked)')[:1500],
        notes=(notes or '(none)')[:600],
        learnings=learnings_text,
    )
    raw = _call_ollama(payload, timeout=120)
    spec = _extract_json(raw)
    if spec:
        spec.setdefault('stop_or_proceed', 'PROCEED')
        spec.setdefault('render_target', 'remotion')
        spec.setdefault('complexity_tier', 'medium')
        spec.setdefault('primary_component_family', 'hybrid')
        spec.setdefault('frame_budget_hint', 'standard')
        spec.setdefault('must_have_assets', [])
        spec.setdefault('risk_flags', [])
        return spec
    return {
        'render_target': 'remotion',
        'complexity_tier': 'medium',
        'primary_component_family': 'hybrid',
        'frame_budget_hint': 'standard',
        'fleet_owner_hint': 'Dexter',
        'must_have_assets': [],
        'risk_flags': [],
        'stop_or_proceed': 'PROCEED',
        'stop_reason': '',
    }


# ---------------------------------------------------------------------------
# Stage 2 — Scene outline (timing architecture)
# ---------------------------------------------------------------------------

SCENE_OUTLINE_TEMPLATE = """You are a video production director. Plan the scene structure of a
{length_seconds}-second video. The number of scenes and the duration of each scene
are STORY-DRIVEN: you decide them based on the narration beats.

NARRATION SCRIPT:
{step2_script}

HERMES PRODUCTION SPEC:
{hermes}

DESIGN SYSTEM ENERGY LEVEL: {energy_level}

Rules — what you MUST do:
- Read the script. Identify natural story beats (hook → context → setup → evidence →
  analysis → implication → CTA, plus any sub-beats).
- Decide how many scenes ({scene_count_hint_min}–{scene_count_hint_max}). The TOTAL
  duration MUST equal {length_seconds} seconds (±5%, i.e. {length_min}-{length_max}s).
- Per-scene duration is variable: hook can be 4-7s, evidence beats 15-30s,
  CTA can be 5-10s. Pick what serves the story. Range: 4-30 seconds per scene.
- Avoid uniform 15-second slots. Each scene's duration must be JUSTIFIED by its
  story_function (label what beat it serves).
- Use the documentary arc as a guide, not a fixed shape. Don't pad. Don't squeeze.
- Each scene needs: scene_id (snake_case, descriptive), label (short human title),
  story_function (one of cold_open|context|setup|evidence|analysis|implication|cta|transition),
  duration_seconds, narration_excerpt (the actual chunk of script that plays here),
  visual_focus (what we see).
- frame_start / frame_end will be computed from durations; do not set them yourself.

Output VALID JSON ONLY. No prose:
{{
  "total_seconds": <float — must be within {length_min}-{length_max}s>,
  "fps": 30,
  "scene_count": <integer — your call>,
  "scenes": [
    {{
      "scene_id":          "snake_case_descriptive",
      "label":             "Short Human Title",
      "story_function":    "cold_open|context|setup|evidence|...",
      "duration_seconds":  <float 4-30, story-justified>,
      "narration_excerpt": "<the exact script chunk for this scene>",
      "visual_focus":      "<primary visual element / metaphor>"
    }},
    ...
  ]
}}
"""


def _scene_count_hint(length_seconds: int) -> tuple[int, int]:
    """Sane bounds for how many scenes a video of this length should have.
    These are HINTS to the LLM, not hard constraints. The LLM picks based on
    story beats; we only reject if the LLM goes wildly out of range.
    """
    if length_seconds <= 30:   return (3, 6)
    if length_seconds <= 60:   return (4, 8)
    if length_seconds <= 180:  return (8, 16)
    if length_seconds <= 300:  return (12, 24)
    if length_seconds <= 600:  return (20, 50)
    return (30, 90)            # 10+ minute long-form


def _validate_story_outline(spec: dict, length_seconds: int) -> tuple[bool, str]:
    """Story-driven constraints — much looser than the old fixed-count check.
    Pass conditions:
      - scenes is a non-empty list
      - total duration within ±5% of target
      - every scene has duration_seconds in [4, 30]
      - every scene has narration_excerpt (story-driven means script-anchored)
    """
    scenes = spec.get('scenes') or []
    if not scenes or not isinstance(scenes, list):
        return False, 'no scenes returned'
    total = sum(float(s.get('duration_seconds') or 0) for s in scenes)
    lo, hi = length_seconds * 0.95, length_seconds * 1.05
    if not (lo <= total <= hi):
        return False, f'total {total:.1f}s outside target ±5% ({lo:.0f}-{hi:.0f}s)'
    for i, s in enumerate(scenes):
        d = float(s.get('duration_seconds') or 0)
        if not (4.0 <= d <= 30.0):
            return False, f'scene {i} duration {d}s outside 4-30s'
        if not (s.get('narration_excerpt') or '').strip():
            return False, f'scene {i} missing narration_excerpt'
    return True, 'ok'


def scene_outline(hermes: dict, step2_script: str = '',
                  targets: dict | None = None) -> dict:
    """Story-driven scene outline.

    The LLM analyzes the script and decides: how many scenes, per-scene duration,
    per-scene story function. Hook ≠ Evidence ≠ CTA in length — duration follows
    the beat, not a uniform 15s grid.

    Falls back to deterministic_scene_outline() ONLY if the LLM fails twice
    (bad JSON, total duration off by >5%, or scene durations out of range).
    """
    targets = targets or compute_scene_targets(TARGET_SECONDS)
    length_seconds = int(targets['length_seconds'])
    hint_min, hint_max = _scene_count_hint(length_seconds)
    ds_energy = hermes.get('primary_component_family', 'hybrid')

    payload = SCENE_OUTLINE_TEMPLATE.format(
        step2_script=(step2_script or '')[:2000],
        hermes=json.dumps(hermes, indent=2)[:600],
        energy_level=ds_energy,
        length_seconds=length_seconds,
        scene_count_hint_min=hint_min,
        scene_count_hint_max=hint_max,
        length_min=int(length_seconds * 0.95),
        length_max=int(length_seconds * 1.05),
    )

    spec = None
    last_err = ''
    for attempt in range(2):
        raw = _call_ollama(payload, timeout=180)
        candidate = _extract_json(raw) or {}
        ok, why = _validate_story_outline(candidate, length_seconds)
        if ok:
            spec = candidate
            break
        last_err = why
        # Re-prompt with the failure reason so the LLM can fix it
        payload = (payload +
                   f'\n\nPREVIOUS ATTEMPT REJECTED — reason: {why}. '
                   f'Adjust durations so total = {length_seconds}s ±5% and '
                   f'every scene is 4-30s. Use story beats, not uniform slots.')

    if spec is None:
        # Last-resort fallback so the pipeline never deadlocks
        print(f'  [scene_outline] LLM failed twice ({last_err}); '
              f'falling back to deterministic outline')
        return deterministic_scene_outline(step2_script, targets)

    # Compute frame timing from the LLM's chosen durations
    scenes = spec.get('scenes', [])
    cursor = 0
    for s in scenes:
        dur = float(s.get('duration_seconds') or 0)
        s['frame_start'] = cursor
        s['frame_end'] = cursor + int(round(dur * FPS))
        s.setdefault('editable', True)
        s.setdefault('story_function', s.get('story_function') or 'beat')
        cursor = s['frame_end']
    spec['total_frames'] = cursor
    spec['total_seconds'] = round(cursor / FPS, 1)
    spec['target_seconds'] = length_seconds
    spec['scene_count'] = len(scenes)
    spec.setdefault('fps', FPS)
    spec.setdefault('source', 'llm-story-driven')
    return spec


# ---------------------------------------------------------------------------
# Stage 3 — Draft scene manifest
# ---------------------------------------------------------------------------

SCENE_MANIFEST_TEMPLATE = """You are a Remotion video engineer. Produce a complete scene manifest
for a {length_seconds}-second video. Each scene maps to a Remotion composition component.

DESIGN SYSTEM (visual language, MANDATORY — all scenes MUST use these colors/fonts/motion):
{design_system}

SCENE OUTLINE (timing is FIXED — do NOT change durations):
{outline}

REMOTION COMPONENT TYPES available: {component_types}

INSTALLED TOOLS (prefer these where applicable):
{scene_tools}

For each scene produce:
- component_type: one of the available types above
- animation_in: how the scene enters (use ONLY: transform/opacity/clip-path/filter — NO width/height/top/left)
- animation_out: how the scene exits (same constraint)
- bg_color: hex from design system palette
- text_color: hex from design system palette
- accent_color: hex from design system palette (optional)
- layout: 2-word layout description (e.g. "centered-vertical", "split-horizontal", "full-bleed")
- assets: list of required assets with name/type/dimensions/source_hint
- jsx_blueprint: 3-5 line JSX skeleton (Remotion-compatible, no imports needed)
- notes: any render-time constraints

Output VALID JSON ONLY:
{{
  "render_target": "{render_target}",
  "fps": 30,
  "resolution": {{"width": 1920, "height": 1080}},
  "scenes": {{
    "hook": {{
      "component_type": "TypographyHero",
      "animation_in": "opacity 0→1 over 15 frames",
      "animation_out": "transform translateY(-40px) + opacity 1→0 over 10 frames",
      "bg_color": "#000000",
      "text_color": "#FFFFFF",
      "accent_color": "#F7931A",
      "layout": "centered-vertical",
      "assets": [
        {{"name": "hook_headline", "type": "text", "dimensions": "1720x400", "source_hint": "design system display font"}}
      ],
      "jsx_blueprint": "<AbsoluteFill style={{{{background: '#000000'}}}}><Sequence from={{{{0}}}} durationInFrames={{{{heroFrames}}}}><FadeIn><h1 style={{{{...displayFont}}}}>{{{{headline}}}}</h1></FadeIn></Sequence></AbsoluteFill>",
      "notes": "Ensure font preloaded with delayRender/continueRender"
    }},
    "thesis": {{ ... }},
    "evidence_1": {{ ... }},
    "evidence_2": {{ ... }},
    "implication": {{ ... }},
    "cta": {{ ... }}
  }},
  "shared_props": {{
    "palette": ["<hex1>", "<hex2>", "<hex3>"],
    "display_font": "<font name>",
    "body_font": "<font name>",
    "transition_frames": 8,
    "easing": "cubic-bezier(0.16, 1, 0.3, 1)"
  }},
  "asset_manifest": [
    {{"scene": "hook", "name": "hook_headline", "type": "text", "dimensions": "1720x400", "format": "text", "source_hint": "generated"}}
  ]
}}
"""


def deterministic_scene_manifest(hermes: dict, outline: dict, design_system: dict,
                                 targets: dict) -> dict:
    palette = design_system.get('color_palette') or []
    colors = [c.get('hex') for c in palette if isinstance(c, dict) and c.get('hex')]
    if not colors:
        colors = ['#020617', '#E2E8F0', '#00D4FF', '#00E676']
    display_font = (design_system.get('typography') or {}).get('display_font') or 'Inter'
    body_font = (design_system.get('typography') or {}).get('body_font') or 'Inter'
    component_cycle = ['TypographyHero', 'DataViz', 'SplitFrame', 'ChartReveal',
                       'TimelineScroll', 'StatCounter', 'FullBleedMedia', 'Kinetic']
    scenes_out: dict = {}
    asset_manifest: list[dict] = []
    for i, scene in enumerate(outline.get('scenes') or []):
        sid = scene.get('scene_id') or f'scene_{i + 1:03d}'
        component_type = component_cycle[i % len(component_cycle)]
        bg = colors[i % len(colors)]
        accent = colors[(i + 2) % len(colors)]
        text = colors[1] if len(colors) > 1 else '#E2E8F0'
        prompt = (
            f"16:9 YouTube production still, {scene.get('label', sid)}, "
            f"{scene.get('visual_focus', '')}, story beat: {scene.get('narration_excerpt', '')[:220]}, "
            f"style system from Step 3, cinematic professional composition, no gibberish text, "
            f"no broken UI, clean editorial lighting, 1920x1080"
        )
        motion_prompt = (
            f"Animate this still for {scene.get('duration_seconds', targets['scene_seconds'])} seconds. "
            f"Stable subject and layout, subtle camera push, professional YouTube documentary motion, "
            f"keep all text readable, do not invent new logos or facts."
        )
        scenes_out[sid] = {
            **scene,
            'component_type': component_type,
            'animation_in': 'opacity 0 to 1 plus transform translateY(18px) over 18 frames',
            'animation_out': 'opacity 1 to 0 plus transform translateY(-12px) over 12 frames',
            'bg_color': bg,
            'text_color': text,
            'accent_color': accent,
            'layout': 'safe-area 160px, centered editorial composition, 16:9',
            'assets': [
                {'name': f'{sid}_hero_image', 'type': 'image', 'dimensions': '1920x1080',
                 'format': 'jpg', 'source_hint': 'quality-first generated image'},
                {'name': f'{sid}_motion_clip', 'type': 'video', 'dimensions': '1920x1080',
                 'format': 'mp4', 'source_hint': 'image-to-video from hero image'},
            ],
            'image_prompt': prompt,
            'motion_prompt': motion_prompt,
            'jsx_blueprint': '<AbsoluteFill><OffthreadVideoOrImage scene={scene} /></AbsoluteFill>',
            'editable': True,
            'notes': 'Generated from deterministic long-form scene plan; enrich with premium image and I2V provider.',
        }
        asset_manifest.extend({'scene': sid, **a} for a in scenes_out[sid]['assets'])
    return {
        'render_target': hermes.get('render_target', 'remotion'),
        'fps': FPS,
        'resolution': {'width': 1920, 'height': 1080},
        'target_seconds': targets['length_seconds'],
        'scene_seconds': targets['scene_seconds'],
        'scene_count': len(scenes_out),
        'run_mode': targets.get('run_mode', 'standard'),
        'scenes': scenes_out,
        'shared_props': {
            'palette': colors,
            'display_font': display_font,
            'body_font': body_font,
            'motion_language': 'quality-first, story-specific, editable per-scene clips',
        },
        'asset_manifest': asset_manifest,
        'visual_generation_policy': {
            'image_order': ['gpt-image-1', 'higgsfield', 'fal', 'comfyui', 'siegfried', 'local-procedural'],
            'video_order': ['seedance', 'higgsfield', 'fal', 'comfyui', 'local-ffmpeg'],
            'pollinations_allowed': targets.get('run_mode') != 'production-long-form',
        },
    }


def draft_scene_manifest(hermes: dict, outline: dict, design_system: dict,
                         step2_script: str = '', mode: str = 'fast',
                         harvest: dict | None = None,
                         targets: dict | None = None) -> dict:
    targets = targets or compute_scene_targets(TARGET_SECONDS)
    if int(targets.get('scene_count', 0)) > 12:
        return deterministic_scene_manifest(hermes, outline, design_system, targets)
    harvest = harvest or {}
    component_types = ', '.join(sorted(VALID_COMPONENT_TYPES))
    payload = SCENE_MANIFEST_TEMPLATE.format(
        design_system=json.dumps(design_system, indent=2)[:2000],
        outline=json.dumps(outline, indent=2)[:1500],
        render_target=hermes.get('render_target', 'remotion'),
        component_types=component_types,
        scene_tools=json.dumps(harvest.get('scene_tools', []), indent=2)[:800],
        length_seconds=targets['length_seconds'],
    )
    if mode == 'deep' and _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
        text = _call_perplexity(payload)
        if text and not text.startswith('_('):
            spec = _extract_json(text)
            if spec:
                return spec
    raw = _call_ollama(payload, timeout=300)
    return _extract_json(raw) or {}


# ---------------------------------------------------------------------------
# Stage 4 — Validators
# ---------------------------------------------------------------------------

def validate_manifest(manifest: dict, outline: dict,
                      targets: dict | None = None) -> dict:
    targets = targets or compute_scene_targets(TARGET_SECONDS)
    grades: dict = {}
    issues: list = []

    total_s = outline.get('total_seconds', 0)
    target_s = float(targets.get('length_seconds', TARGET_SECONDS))
    tolerance = max(1.0, target_s * 0.02)
    delta = abs(float(total_s or 0) - target_s)
    timing_ok = delta <= tolerance
    grades['timing_grade'] = 'GREEN' if timing_ok else ('YELLOW' if delta <= tolerance * 2 else 'RED')
    if not timing_ok:
        issues.append(f'Total duration {total_s}s outside {target_s}s target')

    scenes = manifest.get('scenes') or {}
    present = set(scenes.keys()) if isinstance(scenes, dict) else set()
    expected_ids = _scene_ids(int(targets.get('scene_count', len(GDS_SECTIONS))))
    missing = [sid for sid in expected_ids if sid not in present]
    grades['coverage_grade'] = 'GREEN' if not missing else 'RED'
    if missing:
        issues.append(f'Missing scenes: {", ".join(missing[:10])}')

    bad_components = []
    for sid, scene in (scenes.items() if isinstance(scenes, dict) else []):
        ct = scene.get('component_type', '')
        if ct not in VALID_COMPONENT_TYPES:
            bad_components.append(f'{sid}:{ct}')
    grades['component_grade'] = 'GREEN' if not bad_components else 'YELLOW'
    if bad_components:
        issues.append(f'Unknown component types: {", ".join(bad_components[:10])}')

    motion_violations = []
    for sid, scene in (scenes.items() if isinstance(scenes, dict) else []):
        for anim_key in ('animation_in', 'animation_out'):
            anim = (scene.get(anim_key) or '').lower()
            for prop in LAYOUT_BOUND:
                if prop in anim:
                    motion_violations.append(f'{sid}.{anim_key}: {prop}')
    grades['motion_grade'] = 'GREEN' if not motion_violations else 'RED'
    if motion_violations:
        issues.append(f'Layout-bound animation properties: {"; ".join(motion_violations[:3])}')

    asset_manifest = manifest.get('asset_manifest', [])
    scenes_with_assets = {a.get('scene') for a in asset_manifest if a.get('scene')}
    asset_coverage = len(scenes_with_assets) / max(len(expected_ids), 1)
    grades['asset_grade'] = 'GREEN' if asset_coverage >= 0.8 else ('YELLOW' if asset_coverage >= 0.5 else 'RED')
    if asset_coverage < 0.8:
        issues.append(f'Asset manifest covers only {len(scenes_with_assets)}/{len(expected_ids)} scenes')

    shared = manifest.get('shared_props') or {}
    has_palette = bool(shared.get('palette'))
    has_fonts = bool(shared.get('display_font') and shared.get('body_font'))
    grades['shared_props_grade'] = 'GREEN' if (has_palette and has_fonts) else 'YELLOW'
    if not has_palette:
        issues.append('shared_props.palette missing')
    if not has_fonts:
        issues.append('shared_props fonts missing')

    overall_reds = sum(1 for g in grades.values() if g == 'RED')
    overall_yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    return {
        'grades': grades,
        'issues': issues,
        'overall_reds': overall_reds,
        'overall_yellows': overall_yellows,
        'timing_seconds': total_s,
        'target_seconds': target_s,
        'scene_count': len(present),
        'expected_scene_count': len(expected_ids),
        'asset_count': len(asset_manifest),
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Dev — render feasibility reviewer',
        'lens_template': 'Can every scene in this manifest be implemented in Remotion within the frame budget? Check component_type choices, animation_in/out use compositor-only properties, jsx_blueprint is syntactically plausible, shared_props are self-consistent. Flag anything that would cause a render crash at 30fps.',
    },
    'memo': {
        'role': 'PM — timing and pacing reviewer',
        'lens_template': 'Do the scene durations match the selected video target and narration cadence? Check that evidence scenes get enough time for the data to land. Flag any scene that feels rushed, bloated, or disconnected from its narration_excerpt.',
    },
    'sienna': {
        'role': 'Domain Specialist — visual authenticity reviewer',
        'lens_template': 'Do the component types, layouts, and asset choices match the visual archetype and domain? A dark-fintech video should not use warm-domestic component types. Check that the scene palette matches the design system. Flag any scene that breaks brand coherence.',
    },
    'nano': {
        'role': 'Agent Creator — viewer engagement reviewer',
        'lens_template': 'Does the scene sequence hold a viewer for the full selected length? The opening must grab attention quickly, every 15-second beat needs a story purpose, and CTA must be distinct and memorable. Evidence scenes need visual variety without becoming random filler. Flag drop-off risks.',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent} ({role}).

Review this scene manifest for the video. Focus only on your lens:
{lens}

SCENE MANIFEST (capped):
{manifest_summary}

VALIDATORS:
{validators}

Output Markdown ONLY with these exact sections:
### {agent_cap} — what to fix
(2-4 sharp bullets. Be specific about which scene and what to change.)

### {agent_cap} — verdict
GREEN-LIGHT | YELLOW-LIGHT | RED-LIGHT — one sentence reason.

### {agent_cap} — if I owned this
(1 concrete first move you would make.)
"""


def _review_one(agent: str, cfg: dict, manifest: dict, validators: dict) -> tuple[str, str]:
    manifest_summary = json.dumps({
        'render_target': manifest.get('render_target'),
        'fps': manifest.get('fps'),
        'resolution': manifest.get('resolution'),
        'scenes': {k: {
            'component_type': v.get('component_type'),
            'animation_in': v.get('animation_in'),
            'layout': v.get('layout'),
            'asset_count': len(v.get('assets') or []),
        } for k, v in (manifest.get('scenes') or {}).items()},
        'shared_props': manifest.get('shared_props'),
    }, indent=2)[:2500]

    payload = FLEET_REVIEW_TEMPLATE.format(
        agent=agent.capitalize(),
        agent_cap=agent.capitalize(),
        role=cfg['role'],
        lens=cfg['lens_template'],
        manifest_summary=manifest_summary,
        validators=json.dumps(validators, indent=2)[:800],
    )
    text = _call_ollama(payload, timeout=120)
    return agent, text


def fleet_review(manifest: dict, validators: dict) -> dict:
    reviews: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_review_one, agent, cfg, manifest, validators): agent
            for agent, cfg in FLEET_REVIEWERS.items()
        }
        for fut in as_completed(futures):
            agent = futures[fut]
            try:
                _, text = fut.result()
                reviews[agent] = text
            except Exception as e:
                reviews[agent] = f'_(review error: {e})_'

    ordered = {a: reviews.get(a, '') for a in ['dexter', 'memo', 'sienna', 'nano']}
    verdicts: dict = {'GREEN': 0, 'YELLOW': 0, 'RED': 0}
    for text in ordered.values():
        m = re.search(r'(GREEN|YELLOW|RED)-?LIGHT', text, re.IGNORECASE)
        if m:
            verdicts[m.group(1).upper()] += 1
    return {'reviews': ordered, 'verdicts': verdicts}


# ---------------------------------------------------------------------------
# Quality rating
# ---------------------------------------------------------------------------

def compute_quality(validators: dict, fleet: dict, convergence_passes: int) -> dict:
    reds = validators.get('overall_reds', 0)
    yellows = validators.get('overall_yellows', 0)
    fr = fleet.get('verdicts', {})
    stars = 5.0
    if reds >= 2:
        stars -= 2.0
    elif reds == 1:
        stars -= 1.0
    if yellows >= 2:
        stars -= 0.5
    if fr.get('RED', 0) >= 2:
        stars -= 2.0
    elif fr.get('RED', 0) == 1:
        stars -= 1.0
    if fr.get('YELLOW', 0) >= 2:
        stars -= 0.5
    if convergence_passes >= 2:
        stars -= 0.5
    stars = max(1.0, min(5.0, stars))
    if stars >= 5.0:
        label = 'Production-ready — advance to Audio'
    elif stars >= 4.0:
        label = 'Strong — refine to 5★ before Audio'
    elif stars >= 3.0:
        label = 'Needs refinement — re-calibrate'
    else:
        label = 'Major issues — use Auto-loop'
    reasons = []
    if reds:
        reasons.append(f'{reds} validator RED(s)')
    if fr.get('RED'):
        reasons.append(f'{fr["RED"]} fleet RED(s)')
    if not reasons:
        reasons.append('validators + fleet aligned')
    return {'stars': round(stars, 1), 'label': label, 'reasons': reasons}


# ---------------------------------------------------------------------------
# Harvest — installed scene production tools
# ---------------------------------------------------------------------------

def harvest_scene_tools() -> list[dict]:
    tools = [
        ('node',       'remotion',  'Node.js runtime (required for Remotion)'),
        ('npm',        'remotion',  'Node package manager (Remotion install)'),
        ('npx',        'remotion',  'npx runner (remotion render)'),
        ('ffmpeg',     'rendering', 'Video mux/encode for final compose'),
        ('manim',      'animation', 'Math animation engine (Python)'),
        ('blender',    'rendering', '3D rendering for complex scenes'),
        ('inkscape',   'assets',    'SVG asset creation'),
        ('magick',     'assets',    'ImageMagick v7 image processing'),
        ('imagemagick','assets',    'ImageMagick legacy'),
    ]
    out = []
    for bin_name, category, desc in tools:
        path = shutil.which(bin_name) or ''
        if path:
            out.append({'name': bin_name, 'category': category, 'description': desc, 'path': path})
    return out


def harvest_remotion_refs() -> list[dict]:
    try:
        result = subprocess.run(
            ['gh', 'search', 'repos', 'remotion video template react',
             '--sort', 'stars', '--limit', '6',
             '--json', 'nameWithOwner,description,stargazerCount,url'],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout or '[]')
    except Exception:
        return []


def harvest_oss_registry_scenes() -> str:
    try:
        from .discovery import registry_for_steps
        return registry_for_steps(steps=['step4_scenes', 'step7_render'], max_tools=12)
    except Exception:
        return '(OSS registry unavailable)'


def harvest_step4(hermes: dict) -> dict:
    out: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(harvest_scene_tools): 'scene_tools',
            pool.submit(harvest_remotion_refs): 'remotion_refs',
            pool.submit(harvest_oss_registry_scenes): 'oss_registry',
        }
        for fut in as_completed(futures):
            try:
                out[futures[fut]] = fut.result()
            except Exception as e:
                out[futures[fut]] = [{'error': str(e)}]
    return out


# ---------------------------------------------------------------------------
# Convergence rewrite
# ---------------------------------------------------------------------------

REWRITE_TEMPLATE = """The scene manifest has RED-LIGHT critiques. Rewrite the manifest to fix them.
Only output the corrected FULL manifest JSON (same schema as before).

CURRENT MANIFEST:
{manifest}

RED-LIGHT CRITIQUES:
{critiques}

VALIDATOR ISSUES:
{issues}

Output VALID JSON ONLY — full corrected manifest:
"""


def _rewrite_manifest(manifest: dict, fleet: dict, validators: dict) -> dict:
    red_critiques = '\n\n'.join(
        text for agent, text in fleet.get('reviews', {}).items()
        if re.search(r'RED-?LIGHT', text, re.IGNORECASE)
    )[:3000]
    issues = '; '.join(validators.get('issues', []))[:500]
    payload = REWRITE_TEMPLATE.format(
        manifest=json.dumps(manifest, indent=2)[:4000],
        critiques=red_critiques,
        issues=issues,
    )
    raw = _call_ollama(payload, timeout=300)
    return _extract_json(raw) or manifest


# ---------------------------------------------------------------------------
# Post-research learnings
# ---------------------------------------------------------------------------

def step4_post_research(result: dict, user_notes: str = '') -> dict:
    manifest = result.get('manifest') or {}
    validators = result.get('validators') or {}
    rating = result.get('quality_rating') or {}

    prompt = f"""Extract concise learnings from this Step 4 scene production run.

MANIFEST SUMMARY: {json.dumps({'render_target': manifest.get('render_target'), 'scene_count': len(manifest.get('scenes') or {})}, indent=2)[:400]}
VALIDATORS: {json.dumps(validators.get('grades', {}), indent=2)[:300]}
QUALITY: {rating.get('stars')}★ — {rating.get('label')}
USER NOTES: {user_notes or '(none)'}

Output VALID JSON ONLY:
{{
  "what_worked": ["<1-3 scene patterns that scored well>"],
  "what_failed": ["<1-3 patterns that caused RED verdicts>"],
  "component_lessons": ["<component type usage lessons>"],
  "timing_lessons": ["<timing / duration lessons>"],
  "next_video_recommendations": ["<1-2 recommendations for next run>"]
}}"""

    raw = _call_ollama(prompt, timeout=90)
    record: dict = _extract_json(raw) or {}
    record['kind'] = 'step4_advance'
    record['quality_rating'] = rating
    record['convergence_passes'] = result.get('convergence_passes', 0)
    record['fleet_verdicts'] = (result.get('fleet') or {}).get('verdicts', {})
    record['user_notes'] = user_notes

    try:
        from .learnings import record_learning
        record_learning(record)
    except Exception:
        pass
    return record


# ---------------------------------------------------------------------------
# Advise (for Auto-loop)
# ---------------------------------------------------------------------------

def step4_advise(result: dict) -> dict:
    manifest = result.get('manifest') or {}
    validators = result.get('validators') or {}
    fleet = result.get('fleet') or {}
    rating = result.get('quality_rating') or {}
    stars = rating.get('stars', 3.0)

    prompt = f"""A Step 4 scene manifest scored {stars}★. Diagnose the top issue and write focused
refinement notes (max 120 words) to give the engine on the next run.

VALIDATORS: {json.dumps(validators.get('grades', {}), indent=2)[:400]}
VALIDATOR ISSUES: {'; '.join(validators.get('issues', []))[:300]}
FLEET VERDICTS: {json.dumps(fleet.get('verdicts', {}), indent=2)[:200]}

Output VALID JSON ONLY:
{{
  "diagnosis": "<one sentence: root cause of the low score>",
  "focused_notes": "<120-word max refinement notes to inject into the next run>"
}}"""

    raw = _call_ollama(prompt, timeout=90)
    return _extract_json(raw) or {'diagnosis': 'Unknown issue', 'focused_notes': 'Refine scene timing and asset coverage.'}


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run_step4(step2_script: str = '', design_system: dict | None = None,
              mode: str = 'fast', notes: str = '',
              prior_manifest: dict | None = None,
              max_convergence: int = 2,
              project: str = 'default',
              length_seconds: int = TARGET_SECONDS,
              scene_seconds: int = DEFAULT_SCENE_SECONDS) -> dict:
    started = time.time()
    stage_times: dict = {}
    design_system = design_system or {}
    targets = compute_scene_targets(length_seconds, scene_seconds)

    # Stage 1: Hermes
    t = time.time()
    hermes = hermes_preroute(design_system=design_system, step2_script=step2_script, notes=notes)
    stage_times['hermes_preroute'] = round(time.time() - t, 1)
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'stopped': True, 'hermes': hermes,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # Stage 2a + 2b: Scene outline + harvest in parallel
    harvest: dict = {}
    outline: dict = {}
    t = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outline_fut = pool.submit(scene_outline, hermes, step2_script, targets)
        harvest_fut = pool.submit(harvest_step4, hermes)
        outline = outline_fut.result(timeout=180)
        harvest = harvest_fut.result(timeout=30)
    stage_times['scene_outline_and_harvest'] = round(time.time() - t, 1)

    # Stage 3: Draft manifest
    t = time.time()
    manifest = draft_scene_manifest(
        hermes=hermes, outline=outline, design_system=design_system,
        step2_script=step2_script, mode=mode, harvest=harvest,
        targets=targets,
    )
    stage_times['draft_manifest'] = round(time.time() - t, 1)

    # Stage 4: Validate
    t = time.time()
    validators = validate_manifest(manifest, outline, targets)
    stage_times['validate'] = round(time.time() - t, 1)

    # Stage 5: Fleet review + convergence loop
    convergence_passes = 0
    max_conv = max_convergence
    while convergence_passes <= max_conv:
        t = time.time()
        fleet = fleet_review(manifest, validators)
        stage_times[f'fleet_review_pass_{convergence_passes + 1}'] = round(time.time() - t, 1)

        red_count = fleet.get('verdicts', {}).get('RED', 0)
        if red_count > 0 and convergence_passes < max_conv:
            t = time.time()
            manifest = _rewrite_manifest(manifest, fleet, validators)
            validators = validate_manifest(manifest, outline, targets)
            convergence_passes += 1
            stage_times[f'rewrite_pass_{convergence_passes}'] = round(time.time() - t, 1)
        else:
            break

    quality_rating = compute_quality(validators, fleet, convergence_passes)

    try:
        from .scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=4, fleet=fleet,
            stars=quality_rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=quality_rating.get('label', ''),
        )
    except Exception:
        pass

    try:
        from .skill_db import register_skill
        _prompt = (step2_script or '')[:500]
        _summary = f"step4 scenes · {(quality_rating.get('label') or '')[:80]}"
        _excerpt = {
            'scene_count': len(manifest.get('scenes', [])),
            'total_duration_s': manifest.get('total_duration_s'),
            'fleet_verdicts': fleet.get('verdicts', {}),
        }
        register_skill(
            step=4, prompt=_prompt,
            stars=quality_rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=4, prompt=_prompt, summary=_summary,
                          result_excerpt=_excerpt, stars=quality_rating.get('stars', 0.0))
    except Exception:
        pass

    # Persist the manifest to disk so per-scene editing + isolated regen works
    # later (Phase A scene management). Each scene becomes addressable by
    # scene_id and survives across pipeline runs.
    try:
        from . import scene_store
        # Inject step3 design + topic for the saved record so consumers have
        # full context without re-loading step3
        manifest_for_save = dict(manifest)
        if isinstance(design_system, dict):
            manifest_for_save.setdefault('design_system', design_system)
        manifest_for_save.setdefault('subject', (step2_script or '')[:120])
        scene_store.save_manifest(manifest_for_save, project=project)
    except Exception:
        pass

    return {
        'hermes': hermes,
        'targets': targets,
        'outline': outline,
        'manifest': manifest,
        'harvest': {
            'scene_tools': harvest.get('scene_tools', []),
            'remotion_refs': harvest.get('remotion_refs', []),
        },
        'validators': validators,
        'fleet': fleet,
        'quality_rating': quality_rating,
        'convergence_passes': convergence_passes,
        'elapsed_seconds': round(time.time() - started, 1),
        'stage_times': stage_times,
        'iteration': bool(prior_manifest or notes),
        'mode': mode,
    }
