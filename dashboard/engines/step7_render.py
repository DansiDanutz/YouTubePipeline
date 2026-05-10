#!/usr/bin/env python3.13
"""Step 7 — Render & Compositing engine.

Takes scene manifest (Step 4) + audio spec (Step 5) + subtitle spec (Step 6)
and produces a complete RENDER SPEC: Remotion component scaffold, FFmpeg
assembly pipeline, mux commands, and final output validation plan.

Primary render tool: Remotion (React-based, declarative). FFmpeg for mux/encode.
Output: video_raw.mp4 (Remotion) → muxed with audio → burned subtitles → final.mp4

Pipeline (5 stages, same shape as Steps 1-6):
  Stage 1 — HERMES PRE-ROUTE     (manifest → render engine selection + complexity routing)
  Stage 2 — RENDER OUTLINE       (component scaffold, asset pipeline, encode settings)
  Stage 3 — DRAFT RENDER SPEC    (Remotion commands, FFmpeg pipeline, file manifest)
  Stage 4 — VALIDATE             (command syntax, asset refs, resolution/fps, pipeline integrity)
  Stage 5 — FLEET REVIEW         (technical feasibility / timing / quality / output completeness)

Hard validators:
  • Remotion render command references a real composition ID
  • Resolution exactly 1920×1080
  • FPS exactly 30
  • All 6 GDS scene components referenced in the scaffold
  • FFmpeg mux command combines video + audio
  • Output file is named final.mp4 or zmarty_bitcoin_final.mp4
  • Total frame count matches scene manifest (within ±30 frames)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import urllib.request
import urllib.error

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'
PROJECT_ROOT = Path(__file__).resolve().parents[2]

OLLAMA_HOST  = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL  = os.environ.get('STEP7_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL   = os.environ.get('STEP7_DEEP_MODEL',  'sonar-pro')

TARGET_WIDTH  = 1920
TARGET_HEIGHT = 1080
TARGET_FPS    = 30
TARGET_SECONDS_MIN = 39
TARGET_SECONDS_MAX = 43
GDS_SECTIONS  = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']
STORYV3_SCENE_ORDER = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']


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
            'https://api.perplexity.ai/chat/completions', data=body,
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

HERMES_TEMPLATE = """You are Hermes orchestrating a RENDER & COMPOSITING step. You have:
  - A scene manifest with 6 Remotion component blueprints (Step 4)
  - A locked audio narration spec (Step 5)
  - A subtitle spec (Step 6) — may be skipped if subtitles_enabled=false

Select the render pipeline and surface constraints the render spec MUST respect.

SCENE MANIFEST SUMMARY (from Step 4):
{manifest_summary}

AUDIO OUTPUT (from Step 5): {audio_file}
SUBTITLES ENABLED: {subtitles_enabled}
SUBTITLE FILE (from Step 6): {subtitle_file}

AVAILABLE RENDER TOOLS (detected on this machine):
{render_tools}

RECENT LEARNINGS (from past Step 7 runs):
{learnings}

USER NOTES:
{notes}

Output VALID JSON ONLY:
{{
  "render_engine":          "<remotion | manim | ffmpeg-only>",
  "composition_id":         "<e.g. ZmartyBitcoin — must match Remotion Root.tsx>",
  "output_format":          "<mp4 | webm>",
  "video_codec":            "<h264 | vp9>",
  "audio_codec":            "<aac | opus>",
  "crf":                    <int 18-28 — quality, lower=better>,
  "preset":                 "<ultrafast | fast | medium | slow>",
  "concurrency":            <int 1-4 — Remotion render threads>,
  "mux_strategy":           "<separate — render video then mux audio>",
  "complexity_assessment":  "<simple | moderate | complex>",
  "estimated_render_minutes": <int>,
  "fleet_owner_hint":       "<Dexter | Memo | Sienna | Nano>",
  "stop_or_proceed":        "PROCEED|STOP",
  "stop_reason":            ""
}}
"""


def hermes_preroute(scene_manifest: dict, audio_spec: dict, subtitle_spec: dict,
                    notes: str = '', render_tools: list | None = None,
                    subtitles_enabled: bool = True) -> dict:
    try:
        from .learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        learnings_text = '(learnings store unavailable)'

    manifest_summary = {
        'render_target': scene_manifest.get('render_target', 'remotion'),
        'fps': scene_manifest.get('fps', 30),
        'resolution': scene_manifest.get('resolution', {'width': 1920, 'height': 1080}),
        'total_frames': scene_manifest.get('total_frames', 1230),
        'scene_count': len(scene_manifest.get('scenes', {})),
        'component_family': scene_manifest.get('shared_props', {}).get('display_font', 'general'),
    }

    audio_file = audio_spec.get('output_file') or \
        (audio_spec.get('master_assembly') or {}).get('output_file') or 'narration_piper.wav'
    subtitle_file = subtitle_spec.get('output_srt_file', 'subtitles.srt') if subtitles_enabled else 'N/A'

    tools_summary = json.dumps([t.get('name') for t in (render_tools or [])], indent=2)

    payload = HERMES_TEMPLATE.format(
        manifest_summary=json.dumps(manifest_summary, indent=2)[:400],
        audio_file=audio_file,
        subtitles_enabled=str(subtitles_enabled),
        subtitle_file=subtitle_file,
        render_tools=tools_summary or '(none detected)',
        learnings=learnings_text,
        notes=(notes or '(none)')[:600],
    )
    raw = _call_ollama(payload, timeout=120)
    spec = _extract_json(raw)
    if spec:
        spec.setdefault('stop_or_proceed', 'PROCEED')
        spec.setdefault('render_engine', 'remotion')
        spec.setdefault('composition_id', 'ZmartyBitcoin')
        spec.setdefault('output_format', 'mp4')
        spec.setdefault('video_codec', 'h264')
        spec.setdefault('audio_codec', 'aac')
        spec.setdefault('crf', 22)
        spec.setdefault('preset', 'fast')
        spec.setdefault('concurrency', 2)
        spec.setdefault('mux_strategy', 'separate')
        return spec
    return {
        'render_engine': 'remotion', 'composition_id': 'ZmartyBitcoin',
        'output_format': 'mp4', 'video_codec': 'h264', 'audio_codec': 'aac',
        'crf': 22, 'preset': 'fast', 'concurrency': 2,
        'mux_strategy': 'separate',
        'complexity_assessment': 'moderate', 'estimated_render_minutes': 8,
        'fleet_owner_hint': 'Dexter',
        'stop_or_proceed': 'PROCEED', 'stop_reason': '',
    }


# ---------------------------------------------------------------------------
# Stage 2 — Render outline
# ---------------------------------------------------------------------------

OUTLINE_TEMPLATE = """You are a Remotion video engineer. Create the render pipeline outline
for a 41-second {width}×{height} @ {fps}fps Bitcoin video.

SCENE MANIFEST (Step 4 — 6 GDS scenes with Remotion component blueprints):
{scenes}

SHARED PROPS (fonts, palette, transition_frames):
{shared_props}

HERMES RENDER SETTINGS:
{hermes}

Audio file: {audio_file}
Subtitles: {subtitle_info}

Produce:
1. Remotion component scaffold — one entry per scene with component name + props interface
2. Asset pipeline — all images/icons/fonts needed
3. Encode settings — CRF, preset, codec, bitrate targets
4. Multi-step FFmpeg pipeline — after Remotion renders video_raw.mp4

Output VALID JSON ONLY:
{{
  "project_structure": {{
    "root_file": "src/Root.tsx",
    "composition_id": "<composition id>",
    "entry_point": "src/index.ts",
    "total_frames": <int>,
    "fps": 30,
    "width": {width},
    "height": {height}
  }},
  "component_scaffold": [
    {{
      "scene_id": "hook",
      "component_name": "<PascalCase>",
      "file": "src/scenes/Hook.tsx",
      "props_interface": {{"from": <frame>, "durationInFrames": <int>, "data": {{}}}},
      "remotion_sequence_props": {{"from": <frame>, "durationInFrames": <int>}}
    }}
  ],
  "asset_pipeline": [
    {{"name": "<asset>", "type": "<font|image|icon|data>", "source": "<url or local path>", "destination": "public/<file>"}}
  ],
  "encode_settings": {{
    "crf": <int>,
    "preset": "<fast|medium>",
    "pixel_format": "yuv420p",
    "audio_bitrate": "192k",
    "video_bitrate_target": "3M"
  }},
  "ffmpeg_pipeline": [
    {{"step": 1, "description": "Mux video + audio", "command": "ffmpeg -i video_raw.mp4 -i narration_piper.wav -c:v copy -c:a aac -b:a 192k -shortest video_with_audio.mp4"}},
    {{"step": 2, "description": "Burn-in subtitles (if enabled)", "command": "ffmpeg -i video_with_audio.mp4 -vf \\"subtitles=subtitles.srt\\" -c:a copy final.mp4"}}
  ],
  "estimated_render_minutes": <int>
}}
"""


def render_outline(hermes: dict, scene_manifest: dict, audio_spec: dict,
                   subtitle_spec: dict, subtitles_enabled: bool = True) -> dict:
    scenes = scene_manifest.get('scenes', {})
    shared_props = scene_manifest.get('shared_props', {})
    audio_file = audio_spec.get('output_file') or \
        (audio_spec.get('master_assembly') or {}).get('output_file') or 'narration_piper.wav'
    subtitle_info = subtitle_spec.get('output_srt_file', 'subtitles.srt') if subtitles_enabled else 'disabled'

    payload = OUTLINE_TEMPLATE.format(
        width=TARGET_WIDTH, height=TARGET_HEIGHT, fps=TARGET_FPS,
        scenes=json.dumps(scenes, indent=2)[:2500],
        shared_props=json.dumps(shared_props, indent=2)[:400],
        hermes=json.dumps(hermes, indent=2)[:400],
        audio_file=audio_file,
        subtitle_info=subtitle_info,
    )
    raw = _call_ollama(payload, timeout=180)
    return _extract_json(raw) or {}


# ---------------------------------------------------------------------------
# Stage 3 — Draft render spec
# ---------------------------------------------------------------------------

SPEC_TEMPLATE = """You are a Remotion and FFmpeg expert. Produce the COMPLETE render spec
with all commands ready to execute.

RENDER OUTLINE:
{outline}

HERMES SETTINGS:
{hermes}

INSTALLED TOOLS:
{tools}

Output VALID JSON ONLY:
{{
  "remotion_install_command": "npm install --legacy-peer-deps",
  "remotion_render_command": "npx remotion render src/index.ts {composition_id} --output out/video_raw.mp4 --fps 30 --width {width} --height {height} --concurrency {concurrency} --codec h264 --crf {crf}",
  "ffmpeg_mux_audio": "ffmpeg -i out/video_raw.mp4 -i narration_piper.wav -c:v copy -c:a aac -b:a 192k -shortest out/video_with_audio.mp4",
  "ffmpeg_burn_subtitles": "ffmpeg -i out/video_with_audio.mp4 -vf \\"subtitles=subtitles.srt:force_style='FontName=Arial,FontSize=22'\\",scale={width}:{height} -c:a copy out/final.mp4",
  "ffmpeg_no_subtitles": "ffmpeg -i out/video_with_audio.mp4 -c copy out/final.mp4",
  "ffprobe_validate": "ffprobe -v quiet -print_format json -show_streams out/final.mp4",
  "output_file": "out/final.mp4",
  "root_tsx_scaffold": "<complete Root.tsx content as string>",
  "package_json_dependencies": {{
    "remotion": "^4.0.0",
    "@remotion/bundler": "^4.0.0",
    "@remotion/renderer": "^4.0.0",
    "react": "^18.0.0",
    "react-dom": "^18.0.0"
  }},
  "render_checklist": [
    "npm install done",
    "All scene components exist in src/scenes/",
    "Assets in public/ directory",
    "Remotion renders video_raw.mp4 successfully",
    "FFmpeg mux audio: video_with_audio.mp4",
    "FFmpeg burn subtitles (if enabled): final.mp4",
    "ffprobe confirms 1920x1080 @ 30fps"
  ],
  "estimated_total_minutes": <int>,
  "qa_notes": ["<any render quality risks>"]
}}
"""


def draft_render_spec(hermes: dict, outline: dict, mode: str = 'fast',
                      harvest: dict | None = None) -> dict:
    harvest = harvest or {}
    payload = SPEC_TEMPLATE.format(
        outline=json.dumps(outline, indent=2)[:3000],
        hermes=json.dumps(hermes, indent=2)[:500],
        tools=json.dumps([t.get('name') for t in harvest.get('render_tools', [])], indent=2)[:300],
        composition_id=hermes.get('composition_id', 'ZmartyBitcoin'),
        width=TARGET_WIDTH, height=TARGET_HEIGHT,
        concurrency=hermes.get('concurrency', 2),
        crf=hermes.get('crf', 22),
    )
    if mode == 'deep' and _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
        text = _call_perplexity(payload)
        if text and not text.startswith('_('):
            spec = _extract_json(text)
            if spec:
                return spec
    raw = _call_ollama(payload, timeout=240)
    return _extract_json(raw) or {}


# ---------------------------------------------------------------------------
# Stage 4 — Validators
# ---------------------------------------------------------------------------

def validate_render(spec: dict, outline: dict, hermes: dict,
                    subtitles_enabled: bool = True) -> dict:
    grades: dict = {}
    issues: list = []

    # Remotion render command
    remotion_cmd = spec.get('remotion_render_command', '')
    has_remotion = bool(remotion_cmd and 'remotion' in remotion_cmd.lower())
    grades['remotion_cmd_grade'] = 'GREEN' if has_remotion else 'RED'
    if not has_remotion:
        issues.append('Missing or invalid remotion render command')

    # Resolution in command
    res_ok = f'{TARGET_WIDTH}' in remotion_cmd and f'{TARGET_HEIGHT}' in remotion_cmd
    grades['resolution_grade'] = 'GREEN' if res_ok else ('YELLOW' if has_remotion else 'RED')
    if not res_ok and has_remotion:
        issues.append(f'Render command missing {TARGET_WIDTH}x{TARGET_HEIGHT} resolution flags')

    # FPS in command
    fps_ok = '--fps 30' in remotion_cmd or '--fps=30' in remotion_cmd
    grades['fps_grade'] = 'GREEN' if fps_ok else 'YELLOW'
    if not fps_ok:
        issues.append('Render command missing --fps 30 flag')

    # Component scaffold coverage
    scaffold = outline.get('component_scaffold', [])
    covered_scenes = {s.get('scene_id') for s in scaffold}
    missing = [s for s in GDS_SECTIONS if s not in covered_scenes]
    grades['scaffold_grade'] = 'GREEN' if not missing else ('YELLOW' if len(missing) <= 1 else 'RED')
    if missing:
        issues.append(f'Missing component scaffold for: {", ".join(missing)}')

    # FFmpeg mux command
    mux_cmd = spec.get('ffmpeg_mux_audio', '')
    has_mux = bool(mux_cmd and 'ffmpeg' in mux_cmd and '.wav' in mux_cmd)
    grades['mux_grade'] = 'GREEN' if has_mux else 'RED'
    if not has_mux:
        issues.append('Missing or invalid ffmpeg audio mux command')

    # Subtitle burn-in (only required if subtitles enabled)
    if subtitles_enabled:
        burnin_cmd = spec.get('ffmpeg_burn_subtitles', '')
        has_burnin = bool(burnin_cmd and 'subtitles=' in burnin_cmd)
        grades['subtitle_grade'] = 'GREEN' if has_burnin else 'YELLOW'
        if not has_burnin:
            issues.append('Subtitle burn-in command missing or malformed')
    else:
        no_sub_cmd = spec.get('ffmpeg_no_subtitles', '')
        grades['subtitle_grade'] = 'GREEN' if no_sub_cmd else 'YELLOW'

    # Output file
    output_file = spec.get('output_file', '')
    has_output = bool(output_file and output_file.endswith('.mp4'))
    grades['output_grade'] = 'GREEN' if has_output else 'YELLOW'
    if not has_output:
        issues.append('output_file not set or not .mp4')

    # Frame count sanity
    proj = outline.get('project_structure', {})
    total_frames = proj.get('total_frames', 0)
    expected = TARGET_FPS * 41  # 1230
    frame_ok = abs(total_frames - expected) <= 30 if total_frames else False
    grades['frame_count_grade'] = 'GREEN' if frame_ok else ('YELLOW' if total_frames else 'RED')
    if not frame_ok:
        issues.append(f'Frame count {total_frames} deviates from expected ~{expected}')

    # Package.json dependencies
    has_deps = bool(spec.get('package_json_dependencies', {}).get('remotion'))
    grades['deps_grade'] = 'GREEN' if has_deps else 'YELLOW'
    if not has_deps:
        issues.append('package_json_dependencies missing remotion version')

    overall_reds = sum(1 for g in grades.values() if g == 'RED')
    overall_yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    return {
        'grades': grades, 'issues': issues,
        'overall_reds': overall_reds, 'overall_yellows': overall_yellows,
        'component_count': len(scaffold),
        'covered_scenes': len(covered_scenes),
        'has_remotion_cmd': has_remotion,
        'has_mux_cmd': has_mux,
        'total_frames': total_frames,
        'subtitles_enabled': subtitles_enabled,
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Dev — render pipeline feasibility reviewer',
        'lens': 'Can every command in this render spec actually execute? Check: Remotion render command flags, FFmpeg mux syntax, subtitle burn-in filter, ffprobe validation. Flag anything that would crash at render time or produce a corrupt output file.',
    },
    'memo': {
        'role': 'PM — timeline and delivery reviewer',
        'lens': 'Is the estimated_total_minutes realistic? Are all pipeline steps in the right order (Remotion first, then mux audio, then burn subtitles)? Is there a clear output file path? Flag any step where something could go wrong silently.',
    },
    'sienna': {
        'role': 'Domain Specialist — visual quality reviewer',
        'lens': 'Will this render look professional? Check CRF value (lower=better quality, typical 18-23), pixel format (yuv420p for broad compatibility), bitrate target. Are the asset references correct? Will fonts load inside the Remotion bundle?',
    },
    'nano': {
        'role': 'Agent Creator — completeness reviewer',
        'lens': 'Is the render_checklist complete? Does the Root.tsx scaffold cover all 6 GDS scenes? Is the composition_id consistent across the render command and Root.tsx? Flag any missing step a developer would need to do manually.',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent} ({role}).

Review this render spec. Focus ONLY on your lens:
{lens}

RENDER SPEC SUMMARY:
{spec_summary}

VALIDATORS:
{validators}

Output Markdown ONLY with these exact sections:
### {agent_cap} — what to fix
(2-4 sharp bullets. Be specific about which command or file and what to change.)

### {agent_cap} — verdict
GREEN-LIGHT | YELLOW-LIGHT | RED-LIGHT — one sentence reason.

### {agent_cap} — if I owned this
(1 concrete first move you would make.)
"""


def _review_one(agent: str, cfg: dict, spec: dict, outline: dict, validators: dict) -> tuple[str, str]:
    summary = json.dumps({
        'render_engine': spec.get('render_engine') or outline.get('project_structure', {}).get('composition_id'),
        'composition_id': spec.get('remotion_render_command', '')[:80],
        'has_mux': bool(spec.get('ffmpeg_mux_audio')),
        'has_burnin': bool(spec.get('ffmpeg_burn_subtitles')),
        'output_file': spec.get('output_file'),
        'estimated_minutes': spec.get('estimated_total_minutes'),
        'checklist_items': len(spec.get('render_checklist', [])),
        'scaffold_scenes': [s.get('scene_id') for s in outline.get('component_scaffold', [])],
    }, indent=2)[:1800]

    payload = FLEET_REVIEW_TEMPLATE.format(
        agent=agent.capitalize(), agent_cap=agent.capitalize(),
        role=cfg['role'], lens=cfg['lens'],
        spec_summary=summary,
        validators=json.dumps(validators, indent=2)[:600],
    )
    text = _call_ollama(payload, timeout=120)
    return agent, text


def fleet_review(spec: dict, outline: dict, validators: dict) -> dict:
    reviews: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_review_one, agent, cfg, spec, outline, validators): agent
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
        label = 'Production-ready — advance to Quality Gate'
    elif stars >= 4.0:
        label = 'Strong — refine to 5★ before Quality Gate'
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
# Harvest — render tools + OSS registry
# ---------------------------------------------------------------------------

def harvest_render_tools() -> list[dict]:
    tools = [
        ('node',        'runtime',    'Node.js — required for Remotion'),
        ('npm',         'package',    'npm — package manager for Remotion'),
        ('npx',         'runner',     'npx — Remotion CLI runner'),
        ('ffmpeg',      'encode',     'FFmpeg — video encoding + mux'),
        ('ffprobe',     'analysis',   'FFprobe — stream validation'),
        ('manim',       'animation',  'Manim — data visualization scenes'),
        ('blender',     '3d',         'Blender — 3D scene rendering'),
        ('python3',     'scripting',  'Python — Manim + asset generation'),
        ('convert',     'image',      'ImageMagick convert — image assets'),
        ('inkscape',    'vector',     'Inkscape — SVG asset generation'),
    ]
    out = []
    for bin_name, category, desc in tools:
        path = shutil.which(bin_name) or ''
        if path:
            # Try to get version for key tools
            version = ''
            if bin_name in ('node', 'npm', 'ffmpeg', 'python3'):
                try:
                    v = subprocess.run([bin_name, '--version'], capture_output=True, text=True, timeout=3)
                    version = v.stdout.strip().split('\n')[0][:30]
                except Exception:
                    pass
            out.append({'name': bin_name, 'category': category, 'description': desc,
                        'path': path, 'version': version})
    return out


def harvest_remotion_info() -> dict:
    """Check if a Remotion project exists and what version is installed."""
    cwd = Path.cwd()
    project_dirs = [
        cwd / 'remotion',
        cwd.parent / 'remotion',
        Path.home() / 'Zmarty-Video-Pipeline' / 'remotion',
        Path('/Users/davidai/Zmarty-Video-Pipeline/remotion'),
    ]
    info: dict = {'found': False, 'version': None, 'compositions': []}
    for d in project_dirs:
        pkg = d / 'package.json'
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                deps = {**data.get('dependencies', {}), **data.get('devDependencies', {})}
                info['found'] = True
                info['project_dir'] = str(d)
                info['version'] = deps.get('remotion', 'unknown')
                info['compositions'] = list(deps.keys())[:10]
            except Exception:
                pass
            break
    return info


def harvest_render_github_refs() -> list[dict]:
    try:
        result = subprocess.run(
            ['gh', 'search', 'repos', 'remotion react video render composition bitcoin crypto',
             '--sort', 'stars', '--limit', '6',
             '--json', 'nameWithOwner,description,stargazerCount,url'],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout or '[]')
    except Exception:
        return []


def harvest_oss_registry_render() -> str:
    try:
        from .discovery import registry_for_steps
        return registry_for_steps(steps=['step7_render'], categories=['video-gen', 'rendering'], max_tools=12)
    except Exception:
        return '(OSS registry unavailable)'


def harvest_step7(hermes: dict) -> dict:
    out: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(harvest_render_tools): 'render_tools',
            pool.submit(harvest_remotion_info): 'remotion_info',
            pool.submit(harvest_render_github_refs): 'github_refs',
            pool.submit(harvest_oss_registry_render): 'oss_registry',
        }
        for fut in as_completed(futures):
            try:
                out[futures[fut]] = fut.result()
            except Exception as e:
                out[futures[fut]] = {'error': str(e)}
    return out


# ---------------------------------------------------------------------------
# Convergence rewrite
# ---------------------------------------------------------------------------

REWRITE_TEMPLATE = """The render spec has RED-LIGHT critiques. Rewrite it to fix them.
Output the corrected FULL render spec JSON (same schema).

CURRENT SPEC:
{spec}

RED-LIGHT CRITIQUES:
{critiques}

VALIDATOR ISSUES:
{issues}

Output VALID JSON ONLY — full corrected spec:
"""


def _rewrite_spec(spec: dict, outline: dict, fleet: dict, validators: dict) -> dict:
    red_critiques = '\n\n'.join(
        text for agent, text in fleet.get('reviews', {}).items()
        if re.search(r'RED-?LIGHT', text, re.IGNORECASE)
    )[:3000]
    payload = REWRITE_TEMPLATE.format(
        spec=json.dumps(spec, indent=2)[:3000],
        critiques=red_critiques,
        issues='; '.join(validators.get('issues', []))[:500],
    )
    raw = _call_ollama(payload, timeout=240)
    return _extract_json(raw) or spec


# ---------------------------------------------------------------------------
# Post-research learnings
# ---------------------------------------------------------------------------

def step7_post_research(result: dict, user_notes: str = '') -> dict:
    spec = result.get('render_spec') or {}
    validators = result.get('validators') or {}
    rating = result.get('quality_rating') or {}

    prompt = f"""Extract concise learnings from this Step 7 render run.

RENDER ENGINE: {spec.get('render_engine', 'remotion')}
CRF: {spec.get('crf')} PRESET: {spec.get('preset')}
VALIDATORS: {json.dumps(validators.get('grades', {}), indent=2)[:300]}
QUALITY: {rating.get('stars')}★ — {rating.get('label')}
USER NOTES: {user_notes or '(none)'}

Output VALID JSON ONLY:
{{
  "what_worked": ["<1-3 render patterns that scored well>"],
  "what_failed": ["<1-3 patterns that caused RED verdicts>"],
  "encode_lessons": ["<CRF/preset/codec lessons>"],
  "pipeline_lessons": ["<FFmpeg mux/subtitle pipeline lessons>"],
  "remotion_lessons": ["<Remotion component/scaffold lessons>"],
  "next_video_recommendations": ["<1-2 recommendations for next run>"]
}}"""

    raw = _call_ollama(prompt, timeout=90)
    record: dict = _extract_json(raw) or {}
    record.update({
        'kind': 'step7_advance',
        'quality_rating': rating,
        'convergence_passes': result.get('convergence_passes', 0),
        'fleet_verdicts': (result.get('fleet') or {}).get('verdicts', {}),
        'user_notes': user_notes,
    })
    try:
        from .learnings import record_learning
        record_learning(record)
    except Exception:
        pass
    return record


# ---------------------------------------------------------------------------
# Advise (for Auto-loop)
# ---------------------------------------------------------------------------

def step7_advise(result: dict) -> dict:
    validators = result.get('validators') or {}
    fleet = result.get('fleet') or {}
    rating = result.get('quality_rating') or {}
    stars = rating.get('stars', 3.0)

    prompt = f"""A Step 7 render spec scored {stars}★. Diagnose the top issue and write focused
refinement notes (max 120 words) for the next run.

VALIDATORS: {json.dumps(validators.get('grades', {}), indent=2)[:400]}
ISSUES: {'; '.join(validators.get('issues', []))[:300]}
FLEET VERDICTS: {json.dumps(fleet.get('verdicts', {}))[:200]}

Output VALID JSON ONLY:
{{
  "diagnosis": "<one sentence root cause>",
  "focused_notes": "<120-word max refinement notes>"
}}"""

    raw = _call_ollama(prompt, timeout=90)
    return _extract_json(raw) or {'diagnosis': 'Unknown issue', 'focused_notes': 'Review Remotion command and FFmpeg pipeline.'}


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def _spec_output_filename(subject: str) -> str:
    """Build the spec-conformant output filename: <subject>_<YYYYMMDD>_<HHMMSS>.mp4

    Empty/blank subject -> falls back to 'final.mp4' (legacy behaviour).
    """
    import re as _re
    from datetime import datetime as _dt
    subject = (subject or '').strip()
    if not subject:
        return 'final.mp4'
    slug = _re.sub(r'[^a-zA-Z0-9]+', '-', subject).strip('-').lower()[:60] or 'video'
    stamp = _dt.now().strftime('%Y%m%d_%H%M%S')
    return f'{slug}_{stamp}.mp4'


def _apply_spec_filename(render_spec: dict, subject: str) -> dict:
    """Rewrite render_spec to use the spec-conformant filename when subject is given.

    Touches output_file + the three commands that reference final.mp4. No-op if
    subject is empty (preserves legacy out/final.mp4 behaviour).
    """
    if not (subject or '').strip():
        return render_spec
    new_name = _spec_output_filename(subject)
    new_path = f'out/{new_name}'
    keys = ('output_file', 'ffmpeg_burn_subtitles', 'ffmpeg_no_subtitles', 'ffprobe_validate')
    legacy_paths = ('out/final.mp4', 'out/zmarty_bitcoin_final.mp4', 'final.mp4', 'zmarty_bitcoin_final.mp4')
    for k in keys:
        v = render_spec.get(k)
        if isinstance(v, str):
            for legacy in legacy_paths:
                v = v.replace(legacy, new_path if legacy.startswith('out/') else new_name)
            render_spec[k] = v
    return render_spec


def _safe_output_path(output_file: str) -> Path:
    rel = (output_file or 'out/final.mp4').replace('\\', '/')
    if rel.startswith('/') or ':' in rel:
        raise ValueError('output_file must be a relative path under out/')
    out_path = (PROJECT_ROOT / rel).resolve()
    out_root = (PROJECT_ROOT / 'out').resolve()
    out_path.relative_to(out_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def _scene_text(scene: dict, *keys: str, fallback: str = '') -> str:
    for key in keys:
        value = scene.get(key) if isinstance(scene, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _ensure_storyv3_beats(scene_manifest: dict) -> dict:
    """Ensure the render manifest has editable StoryV3 beats for all scenes."""
    if isinstance(scene_manifest.get('beats'), list) and scene_manifest['beats']:
        for i, beat in enumerate(scene_manifest['beats']):
            if isinstance(beat, dict):
                beat.setdefault('duration_s', 6.0)
                data = beat.setdefault('data', {})
                if isinstance(data, dict):
                    data.setdefault('editable', True)
                    data.setdefault('scene_index', i + 1)
                    data.setdefault('scene_id', data.get('kind') or str(i + 1))
        return {'created': False, 'count': len(scene_manifest['beats'])}

    raw_scenes = scene_manifest.get('scenes') or {}
    if isinstance(raw_scenes, dict):
        scene_pairs = [(sid, raw_scenes.get(sid) or {}) for sid in STORYV3_SCENE_ORDER if isinstance(raw_scenes.get(sid), dict)]
        for sid, scene in raw_scenes.items():
            if sid not in STORYV3_SCENE_ORDER and isinstance(scene, dict):
                scene_pairs.append((sid, scene))
    elif isinstance(raw_scenes, list):
        scene_pairs = [(s.get('scene_id') or s.get('id') or f'scene_{i+1}', s) for i, s in enumerate(raw_scenes) if isinstance(s, dict)]
    else:
        scene_pairs = []

    if not scene_pairs:
        scene_pairs = [(sid, {}) for sid in STORYV3_SCENE_ORDER]

    kind_cycle = ['hook', 'setup', 'conflict', 'breakthrough', 'resolution', 'cta']
    beats: list[dict] = []
    for i, (scene_id, scene) in enumerate(scene_pairs):
        kind = kind_cycle[i % len(kind_cycle)]
        label = _scene_text(scene, 'label', 'name', fallback=scene_id.replace('_', ' ').title())
        headline = _scene_text(scene, 'headline', 'visual_focus', 'narration_excerpt', 'notes', fallback=label)
        duration = float(scene.get('duration_seconds') or scene.get('duration_s') or 6.0) if isinstance(scene, dict) else 6.0
        base = {
            'kind': kind,
            'scene_id': scene_id,
            'editable': True,
            'scene_index': i + 1,
            'eyebrow': label.upper()[:48],
            'headline': headline[:120],
            'visual_focus': _scene_text(scene, 'visual_focus', 'jsx_blueprint', fallback=headline),
            'narration_excerpt': _scene_text(scene, 'narration_excerpt', 'narration', fallback=headline),
            'motion_prompt': _scene_text(scene, 'motion_prompt', 'visual_focus', 'narration_excerpt', fallback=headline),
            'imageUrl': scene.get('imageUrl') or scene.get('image_url') or '',
            'motionClip': scene.get('motionClip') or '',
        }
        if kind == 'hook':
            data = {**base, 'statValue': int(scene.get('stat_value') or 1), 'label': headline[:80], 'subLabel': label}
        elif kind == 'setup':
            data = {**base, 'mark': (label[:1] or '?').upper(), 'name': headline[:48], 'subtitle': label, 'tags': ['Research', 'Scene', 'Editable']}
        elif kind == 'conflict':
            data = {**base, 'bars': [
                {'label': 'SETUP', 'value': 25, 'unit': '%'},
                {'label': 'TENSION', 'value': 50, 'unit': '%'},
                {'label': 'EVIDENCE', 'value': 75, 'unit': '%'},
                {'label': 'PAYOFF', 'value': 100, 'unit': '%'},
            ], 'total': {'label': 'SCENE', 'value': label}}
        elif kind == 'breakthrough':
            data = {**base, 'rows': [
                {'rank': 1, 'name': headline[:54], 'value': 'KEY', 'hero': True},
                {'rank': 2, 'name': label[:54], 'value': 'SUPPORT'},
            ]}
        elif kind == 'resolution':
            data = {**base, 'bigStat': scene.get('big_stat') or '✓', 'bigStatLabel': headline[:64], 'stats': [
                {'v': '1', 'l': 'clear point'},
                {'v': '6', 'l': 'scene arc'},
                {'v': '30', 'l': 'fps'},
            ]}
        else:
            data = {**base, 'url': scene.get('url') or 'Zmarty'}
        beats.append({'duration_s': duration, 'data': data})

    scene_manifest['beats'] = beats
    scene_manifest['editable'] = True
    return {'created': True, 'count': len(beats)}


def _relative_public_media(path: str | Path) -> str:
    try:
        p = Path(path).resolve()
        return p.relative_to((PROJECT_ROOT / 'public').resolve()).as_posix()
    except Exception:
        return str(path).replace('\\', '/')


def _resolve_existing_path(path: str | None) -> Path | None:
    if not path:
        return None
    raw = str(path).replace('\\', '/')
    candidates = []
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([PROJECT_ROOT / raw, PROJECT_ROOT / 'out' / raw, PROJECT_ROOT / 'public' / raw])
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    return None


def _audio_file_from_spec(audio_spec: dict) -> Path | None:
    candidates = [
        audio_spec.get('output_file') if isinstance(audio_spec, dict) else None,
        (audio_spec.get('master_assembly') or {}).get('output_file') if isinstance(audio_spec, dict) else None,
        audio_spec.get('audio_file') if isinstance(audio_spec, dict) else None,
    ]
    for candidate in candidates:
        found = _resolve_existing_path(candidate)
        if found:
            return found
    return None


def _subtitle_file_from_spec(subtitle_spec: dict) -> Path | None:
    candidates = [
        subtitle_spec.get('output_srt_file') if isinstance(subtitle_spec, dict) else None,
        subtitle_spec.get('srt_file') if isinstance(subtitle_spec, dict) else None,
        subtitle_spec.get('output_file') if isinstance(subtitle_spec, dict) else None,
    ]
    for candidate in candidates:
        found = _resolve_existing_path(candidate)
        if found:
            return found
    return None


def _run_subprocess(cmd: list[str], stage: str, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'stage': stage, 'error': f'timeout after {timeout}s', 'cmd': cmd}
    except Exception as e:
        return {'ok': False, 'stage': stage, 'error': f'{type(e).__name__}: {e}', 'cmd': cmd}
    if proc.returncode != 0:
        return {
            'ok': False,
            'stage': stage,
            'exit_code': proc.returncode,
            'stdout': (proc.stdout or '')[-2000:],
            'stderr': (proc.stderr or '')[-2000:],
            'cmd': cmd,
        }
    return {'ok': True, 'stage': stage, 'cmd': cmd}


def _ffmpeg_subtitle_filter(srt_path: Path) -> str:
    s = srt_path.as_posix().replace(':', '\\:').replace("'", "\\'")
    return f"subtitles='{s}':force_style='FontName=Arial,FontSize=22'"


def execute_render(render_spec: dict, scene_manifest: dict | None = None,
                   audio_spec: dict | None = None, subtitle_spec: dict | None = None,
                   subtitles_enabled: bool = True, *, timeout: int = 900) -> dict:
    """Render the actual Remotion composition and verify the produced MP4.

    This intentionally executes a deterministic local command instead of the
    LLM-authored shell snippets in render_spec.
    """
    try:
        out_path = _safe_output_path(render_spec.get('output_file') or 'out/final.mp4')
    except Exception as e:
        return {'ok': False, 'stage': 'path', 'error': str(e)}

    npx = shutil.which('npx') or shutil.which('npx.cmd') or 'npx'
    ffmpeg = shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
    scene_manifest = scene_manifest or {}
    audio_spec = audio_spec or {}
    subtitle_spec = subtitle_spec or {}
    composition_id = 'StoryV3' if isinstance(scene_manifest.get('beats'), list) else 'ZmartyBitcoin'
    raw_video_path = out_path.with_name(out_path.stem + '_raw.mp4')
    muxed_path = out_path.with_name(out_path.stem + '_muxed.mp4')
    render_target = raw_video_path
    audio_file = _audio_file_from_spec(audio_spec)
    subtitle_file = _subtitle_file_from_spec(subtitle_spec) if subtitles_enabled else None
    require_audio = (os.environ.get('STEP7_REQUIRE_AUDIO') or 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
    require_subtitles = subtitles_enabled and (os.environ.get('STEP7_REQUIRE_SUBTITLES') or 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
    if require_audio and not audio_file:
        return {
            'ok': False,
            'stage': 'audio',
            'error': 'Step 7 production render requires Step 5 narration audio; no audio file was found in audio_spec',
        }
    if require_subtitles and not subtitle_file:
        return {
            'ok': False,
            'stage': 'subtitles',
            'error': 'Step 7 production render requires Step 6 subtitles when subtitles are enabled; no subtitle file was found in subtitle_spec',
        }
    if require_subtitles and not ffmpeg:
        return {'ok': False, 'stage': 'tooling', 'error': 'ffmpeg is required to burn subtitles'}
    if not ffmpeg and audio_file:
        return {'ok': False, 'stage': 'tooling', 'error': 'ffmpeg is required to mux narration audio'}
    cmd = [
        npx, 'remotion', 'render',
        'src/index.tsx', composition_id, str(render_target),
        '--overwrite',
    ]
    props_path = None
    if composition_id == 'StoryV3':
        props_path = out_path.parent / 'render_props_storyv3.json'
        props_path.write_text(json.dumps(scene_manifest), encoding='utf-8')
        cmd.extend(['--props', str(props_path)])
    started = time.time()
    stages: list[dict] = []
    render_result = _run_subprocess(cmd, 'render', timeout)
    stages.append(render_result)
    if not render_result.get('ok'):
        render_result['elapsed_seconds'] = round(time.time() - started, 1)
        return render_result
    current = render_target

    if audio_file:
        mux_cmd = [
            ffmpeg, '-y', '-i', str(current), '-i', str(audio_file),
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest',
            str(muxed_path),
        ]
        mux_result = _run_subprocess(mux_cmd, 'mux_audio', timeout)
        stages.append(mux_result)
        if not mux_result.get('ok'):
            mux_result['elapsed_seconds'] = round(time.time() - started, 1)
            return mux_result
        current = muxed_path

    if subtitle_file and ffmpeg:
        sub_cmd = [
            ffmpeg, '-y', '-i', str(current),
            '-vf', _ffmpeg_subtitle_filter(subtitle_file),
            '-c:a', 'copy',
            str(out_path),
        ]
        sub_result = _run_subprocess(sub_cmd, 'burn_subtitles', timeout)
        stages.append(sub_result)
        if not sub_result.get('ok'):
            sub_result['elapsed_seconds'] = round(time.time() - started, 1)
            return sub_result
        current = out_path
    elif current != out_path:
        shutil.copyfile(current, out_path)
    else:
        shutil.copyfile(render_target, out_path)

    if not out_path.exists() or out_path.stat().st_size <= 0:
        return {'ok': False, 'stage': 'file', 'error': 'render finished but output file is missing or empty'}

    ffprobe = shutil.which('ffprobe') or shutil.which('ffprobe.exe')
    probe: dict = {'available': bool(ffprobe)}
    if ffprobe:
        try:
            p = subprocess.run(
                [ffprobe, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', str(out_path)],
                capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace',
            )
            probe['ok'] = p.returncode == 0
            parsed = json.loads(p.stdout or '{}') if p.returncode == 0 else {}
            probe['streams'] = parsed.get('streams', [])
            probe['format'] = parsed.get('format', {})
            if p.returncode != 0:
                probe['error'] = (p.stderr or '')[:500]
        except Exception as e:
            probe = {'available': True, 'ok': False, 'error': str(e)[:500]}

    return {
        'ok': True,
        'stage': 'complete',
        'output_file': str(out_path),
        'raw_video_file': str(raw_video_path),
        'muxed_video_file': str(muxed_path) if audio_file else '',
        'audio_file': str(audio_file) if audio_file else '',
        'subtitle_file': str(subtitle_file) if subtitle_file else '',
        'composition_id': composition_id,
        'props_file': str(props_path) if props_path else '',
        'bytes': out_path.stat().st_size,
        'elapsed_seconds': round(time.time() - started, 1),
        'stages': stages,
        'ffprobe': probe,
    }


# ---------------------------------------------------------------------------
# Per-scene image→video generation (Spec Step 6 / now folded into Step 7).
# Iterates the scene manifest, locates each scene's hero image, and runs the
# video provider chain (ComfyUI Wan 2.1 → Seedance → Higgsfield → fal.ai) to
# produce a 5s motion clip. Results are attached to scene records as
# `motion_clip` so downstream Remotion templates can swap the static <Img>
# for an <OffthreadVideo>. Entire stage is opt-in: skipped when no provider
# is configured.
# ---------------------------------------------------------------------------

def _resolve_scene_hero_image(scene: dict, scene_index: int,
                              project_root: Path) -> str:
    """Find the hero image file for a scene by checking common locations.
    Returns absolute path string or '' if no image found."""
    def _existing_media_path(value: str) -> str:
        if not value or value.startswith(('http://', 'https://')):
            return value
        raw = value.replace('\\', '/')
        p = Path(raw)
        candidates = [p] if p.is_absolute() else [
            project_root / raw.lstrip('/'),
            project_root / 'public' / raw.lstrip('/').removeprefix('public/'),
            project_root / 'out' / raw.lstrip('/'),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return ''

    for key in ('motion_image', 'hero_image', 'image_path', 'imageUrl',
                'hero', 'asset_path', 'image'):
        candidate = scene.get(key) if isinstance(scene, dict) else None
        if isinstance(candidate, str) and candidate:
            found = _existing_media_path(candidate)
            if found:
                return found
    # Asset list fallback — first item with kind='image' or url ending .jpg/.png
    assets = scene.get('assets', []) if isinstance(scene, dict) else []
    for a in assets if isinstance(assets, list) else []:
        path = (a.get('path') or a.get('url') or '') if isinstance(a, dict) else ''
        if path and path.lower().endswith(('.jpg', '.jpeg', '.png')):
            found = _existing_media_path(path)
            if found:
                return found
    # Common output naming conventions (preview_scene1.jpg / ai_preview_scene1.jpg)
    n = scene_index + 1
    out_dir = project_root / 'out'
    for fname in (f'ai_preview_scene{n}.jpg', f'preview_scene{n}.jpg',
                  f'scene_{n:02d}.jpg', f'scene_{n}.jpg'):
        p = out_dir / fname
        if p.exists():
            return str(p)
    # Generated by step_image_gen.py
    p = project_root / 'out' / 'assets' / 'scenes' / f'scene_{n:02d}.jpg'
    if p.exists():
        return str(p)
    return ''


def _scene_clip_prompt(scene: dict, default_prompt: str = '') -> str:
    """Pick the most descriptive text for the motion-clip prompt."""
    if isinstance(scene, dict):
        for key in ('motion_prompt', 'narration', 'narration_excerpt',
                    'visual_focus', 'headline', 'label'):
            v = scene.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:1500]
    return default_prompt or 'subtle cinematic motion, slow zoom, ambient atmosphere'


def _ensure_storyv3_hero_images(scene_manifest: dict, project_root: Path,
                                subject: str = '') -> dict:
    """Generate missing hero images for StoryV3 beats and attach imageUrl props."""
    if not isinstance(scene_manifest.get('beats'), list):
        return {'ran': False, 'reason': 'manifest has no StoryV3 beats', 'images': []}
    beats = scene_manifest.get('beats') or []
    missing = []
    for i, beat in enumerate(beats, start=1):
        data = beat.get('data') if isinstance(beat, dict) else None
        if not isinstance(data, dict):
            continue
        if data.get('imageUrl') and _resolve_scene_hero_image(data, i - 1, project_root):
            continue
        missing.append(i)
    if not missing:
        return {'ran': False, 'reason': 'all beats already have hero images', 'images': []}

    try:
        from .step_image_gen import generate_scene_images
    except Exception as e:
        return {'ran': False, 'reason': f'import failed: {e}', 'images': []}

    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            results = generate_scene_images(
                scene_manifest,
                str(project_root / 'public'),
                topic_summary=subject or (scene_manifest.get('title') or ''),
            )
    except Exception as e:
        return {'ran': False, 'reason': f'image generation failed: {type(e).__name__}: {e}', 'images': []}

    by_scene = {int(r.get('scene', 0)): r for r in results if r.get('scene')}
    for i, beat in enumerate(beats, start=1):
        data = beat.get('data') if isinstance(beat, dict) else None
        r = by_scene.get(i)
        if isinstance(data, dict) and r and r.get('ok') and r.get('path'):
            rel = _relative_public_media(r['path'])
            data['imageUrl'] = rel
            data['image_provider'] = r.get('provider')
    return {
        'ran': True,
        'requested': len(missing),
        'images': results,
        'ok_count': sum(1 for r in results if r.get('ok')),
    }


def _generate_scene_clips(scene_manifest: dict, project_root: Path,
                          out_dir: Path,
                          duration_s: float | None = None,
                          max_workers: int = 2) -> dict:
    """For every scene with a hero image, generate a short motion clip.

    Returns {ran, configured_providers, scenes: [...]} where each scene entry
    carries {scene_id, ok, provider, motion_clip, attempts}. Mutates the
    scene_manifest in place to attach `motion_clip` paths on success.

    Stage is opt-in:
      - Set STEP7_MOTION_CLIPS=off to disable entirely
      - Set STEP7_MOTION_CLIPS=on to force (will still no-op if no provider)
      - Default 'auto' = run when any provider is configured
    """
    mode = (os.environ.get('STEP7_MOTION_CLIPS') or 'auto').lower()
    if mode == 'off':
        return {'ran': False, 'reason': 'disabled by STEP7_MOTION_CLIPS=off',
                'scenes': []}

    try:
        from .step_image_gen import (video_provider_status,
                                     fetch_video_with_fallback)
    except Exception as e:
        return {'ran': False, 'reason': f'import failed: {e}', 'scenes': []}

    vstatus = video_provider_status()
    configured = [name for name in ('comfyui', 'seedance', 'higgsfield', 'fal')
                  if vstatus.get(name, {}).get('available')]
    ffmpeg = shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
    if ffmpeg:
        configured.append('local-ffmpeg')
    if mode == 'auto' and not configured:
        return {'ran': False, 'reason': 'no video provider configured and ffmpeg unavailable',
                'configured_providers': [], 'scenes': []}

    # Normalise the scene list — manifest stores scenes either as a list or as
    # an ordered dict keyed by scene_id.
    raw_scenes = scene_manifest.get('scenes')
    if isinstance(scene_manifest.get('beats'), list):
        scene_items = []
        for i, beat in enumerate(scene_manifest.get('beats') or []):
            data = beat.get('data') if isinstance(beat, dict) else None
            scene = data if isinstance(data, dict) else beat
            scene_id = scene.get('kind') or scene.get('scene_id') or str(i) if isinstance(scene, dict) else str(i)
            scene_items.append((scene_id, scene, beat if isinstance(beat, dict) else None))
    elif isinstance(raw_scenes, dict):
        scene_items = [(sid, scene, None) for sid, scene in raw_scenes.items()]
    elif isinstance(raw_scenes, list):
        scene_items = [(s.get('scene_id') or s.get('id') or str(i), s, None)
                       for i, s in enumerate(raw_scenes)]
    else:
        return {'ran': False, 'reason': 'scene_manifest has no scenes',
                'configured_providers': configured, 'scenes': []}

    scene_count = len(scene_items)
    clips_dir = project_root / 'public' / 'generated' / 'scene_clips'
    clips_dir.mkdir(parents=True, exist_ok=True)

    def _scene_duration(scene: dict, beat: dict | None) -> float:
        candidates = []
        if isinstance(scene, dict):
            candidates.extend([scene.get('duration_seconds'), scene.get('duration_s')])
        if isinstance(beat, dict):
            candidates.extend([beat.get('duration_s'), beat.get('duration_seconds')])
        for value in candidates:
            try:
                seconds = float(value)
                if seconds > 0:
                    return seconds
            except Exception:
                continue
        return float(duration_s or 15.0)

    def _do(idx: int, scene_id, scene: dict, beat: dict | None = None) -> dict:
        if not isinstance(scene, dict):
            return {'scene_id': scene_id, 'ok': False,
                    'reason': 'scene is not a dict'}
        clip_duration = _scene_duration(scene, beat)
        image_path = _resolve_scene_hero_image(scene, idx, project_root)
        if not image_path:
            if ffmpeg:
                out_path = str(clips_dir / f'scene_{idx + 1:03d}.mp4')
                color_cmd = [
                    ffmpeg, '-y',
                    '-f', 'lavfi', '-i', f'color=c=0x0a0a0c:s=1920x1080:d={clip_duration}:r={TARGET_FPS}',
                    '-an', '-pix_fmt', 'yuv420p', out_path,
                ]
                color_result = _run_subprocess(color_cmd, 'scene_color_video', 120)
                if color_result.get('ok') and Path(out_path).exists() and Path(out_path).stat().st_size > 0:
                    public_rel = f'generated/scene_clips/scene_{idx + 1:03d}.mp4'
                    scene['motion_clip'] = out_path
                    scene['motionClip'] = public_rel
                    scene['scene_video'] = public_rel
                    scene['editable'] = True
                    scene['motion_provider'] = 'local-ffmpeg-color'
                    if isinstance(beat, dict):
                        beat.setdefault('data', scene)
                        beat['data'].update({
                            'motion_clip': out_path,
                            'motionClip': public_rel,
                            'scene_video': public_rel,
                            'editable': True,
                            'motion_provider': 'local-ffmpeg-color',
                        })
                    return {
                        'scene': idx + 1,
                        'path': out_path,
                        'provider': 'local-ffmpeg-color',
                        'attempts': [{'provider': 'local-ffmpeg-color', 'ok': True}],
                        'ok': True,
                        'scene_id': scene_id,
                        'duration_s': clip_duration,
                        'fallback': True,
                        'reason': 'no hero image found; generated color clip',
                    }
            return {'scene_id': scene_id, 'ok': False, 'skipped': True,
                    'reason': 'no hero image found'}
        prompt = _scene_clip_prompt(scene)
        out_path = str(clips_dir / f'scene_{idx + 1:03d}.mp4')
        result = fetch_video_with_fallback(image_path, prompt, out_path,
                                           duration_s=clip_duration,
                                           scene_id=idx + 1)
        if not result.get('ok') and ffmpeg:
            fallback_cmd = [
                ffmpeg, '-y',
                '-loop', '1', '-i', image_path,
                '-t', str(clip_duration),
                '-vf', 'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p',
                '-r', str(TARGET_FPS),
                '-an',
                str(out_path),
            ]
            fallback = _run_subprocess(fallback_cmd, 'scene_fallback_video', 180)
            if fallback.get('ok') and Path(out_path).exists() and Path(out_path).stat().st_size > 0:
                result = {
                    'scene': idx + 1,
                    'path': out_path,
                    'provider': 'local-ffmpeg',
                    'attempts': result.get('attempts', []) + [{'provider': 'local-ffmpeg', 'ok': True}],
                    'elapsed': result.get('elapsed', 0),
                    'ok': True,
                    'fallback': True,
                }
        if result.get('ok'):
            public_rel = f'generated/scene_clips/scene_{idx + 1:03d}.mp4'
            scene['motion_clip'] = result['path']
            scene['motionClip'] = public_rel
            scene['scene_video'] = public_rel
            scene['editable'] = True
            scene['motion_provider'] = result.get('provider')
            if isinstance(beat, dict):
                beat.setdefault('data', scene)
                beat['data']['motion_clip'] = result['path']
                beat['data']['motionClip'] = public_rel
                beat['data']['scene_video'] = public_rel
                beat['data']['editable'] = True
                beat['data']['motion_provider'] = result.get('provider')
        return {**result, 'scene_id': scene_id, 'image_path': image_path, 'duration_s': clip_duration}

    results: list[dict] = []
    if max_workers <= 1 or scene_count <= 1:
        for i, (sid, sc, beat) in enumerate(scene_items):
            results.append(_do(i, sid, sc, beat))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {pool.submit(_do, i, sid, sc, beat): i
                    for i, (sid, sc, beat) in enumerate(scene_items)}
            for fut in as_completed(futs):
                results.append(fut.result())
        results.sort(key=lambda r: r.get('scene', 0))

    ok_count = sum(1 for r in results if r.get('ok'))
    return {
        'ran': True,
        'configured_providers': configured,
        'mode': mode,
        'scenes': results,
        'ok_count': ok_count,
        'total': scene_count,
        'all_scenes_have_video': ok_count == scene_count and scene_count > 0,
    }


def run_step7(scene_manifest: dict | None = None, audio_spec: dict | None = None,
              subtitle_spec: dict | None = None, subtitles_enabled: bool = True,
              mode: str = 'fast', notes: str = '',
              prior_render_spec: dict | None = None,
              max_convergence: int = 2,
              project: str = 'default',
              subject: str = '',
              execute: bool = False,
              generate_motion_clips: bool | None = None) -> dict:
    started = time.time()
    stage_times: dict = {}
    scene_manifest = scene_manifest or {}
    audio_spec     = audio_spec or {}
    subtitle_spec  = subtitle_spec or {}
    asset_reports: dict = {}
    beat_report = _ensure_storyv3_beats(scene_manifest)
    asset_reports['storyv3_beats'] = beat_report

    # Stage 1: Hermes
    t = time.time()
    render_tools_quick = harvest_render_tools()
    hermes = hermes_preroute(
        scene_manifest=scene_manifest, audio_spec=audio_spec,
        subtitle_spec=subtitle_spec, notes=notes,
        render_tools=render_tools_quick, subtitles_enabled=subtitles_enabled,
    )
    stage_times['hermes_preroute'] = round(time.time() - t, 1)
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'stopped': True, 'hermes': hermes,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # Stage 2a + 2b: Render outline + full harvest in parallel
    t = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outline_fut = pool.submit(render_outline, hermes, scene_manifest,
                                  audio_spec, subtitle_spec, subtitles_enabled)
        harvest_fut = pool.submit(harvest_step7, hermes)
        outline = outline_fut.result(timeout=200)
        harvest = harvest_fut.result(timeout=35)
    stage_times['outline_and_harvest'] = round(time.time() - t, 1)

    # Stage 3: Draft render spec
    t = time.time()
    render_spec = draft_render_spec(hermes=hermes, outline=outline,
                                    mode=mode, harvest=harvest)
    stage_times['draft_render_spec'] = round(time.time() - t, 1)

    # Stage 4: Validate
    t = time.time()
    validators = validate_render(render_spec, outline, hermes, subtitles_enabled)
    stage_times['validate'] = round(time.time() - t, 1)

    # Stage 5: Fleet review + convergence
    convergence_passes = 0
    while convergence_passes <= max_convergence:
        t = time.time()
        fleet = fleet_review(render_spec, outline, validators)
        stage_times[f'fleet_review_pass_{convergence_passes + 1}'] = round(time.time() - t, 1)

        red_count = fleet.get('verdicts', {}).get('RED', 0)
        if red_count > 0 and convergence_passes < max_convergence:
            t = time.time()
            render_spec = _rewrite_spec(render_spec, outline, fleet, validators)
            validators = validate_render(render_spec, outline, hermes, subtitles_enabled)
            convergence_passes += 1
            stage_times[f'rewrite_pass_{convergence_passes}'] = round(time.time() - t, 1)
        else:
            break

    # Stage 6: Per-scene image→video clip generation (Spec Step 6).
    t = time.time()
    image_report = _ensure_storyv3_hero_images(scene_manifest, PROJECT_ROOT, subject=subject)
    asset_reports['hero_images'] = image_report
    stage_times['hero_images'] = round(time.time() - t, 1)

    # Mutates scene_manifest to attach `motion_clip` paths so the Remotion
    # template (or downstream FFmpeg overlay) can swap static <Img> for
    # <OffthreadVideo>. Auto-skips when no video provider is configured.
    t = time.time()
    if generate_motion_clips is False:
        motion_clips_report = {'ran': False, 'reason': 'generate_motion_clips=False',
                               'scenes': []}
    else:
        motion_clips_report = _generate_scene_clips(
            scene_manifest=scene_manifest,
            project_root=PROJECT_ROOT,
            out_dir=PROJECT_ROOT / 'out',
            duration_s=None,
            max_workers=2,
        )
    stage_times['motion_clips'] = round(time.time() - t, 1)
    # Surface clip references on the render spec so the renderer can pick them up
    if motion_clips_report.get('ran') and motion_clips_report.get('ok_count', 0) > 0:
        render_spec.setdefault('motion_clips', {
            'count': motion_clips_report['ok_count'],
            'paths': [r.get('path') for r in motion_clips_report['scenes']
                      if r.get('ok') and r.get('path')],
            'providers_used': sorted(set(
                r.get('provider') for r in motion_clips_report['scenes']
                if r.get('ok') and r.get('provider')
            )),
        })

    # Spec filename: <subject>_<date>_<time>.mp4 (no-op when subject is empty)
    render_spec = _apply_spec_filename(render_spec, subject)

    quality_rating = compute_quality(validators, fleet, convergence_passes)
    render_execution = execute_render(
        render_spec,
        scene_manifest=scene_manifest,
        audio_spec=audio_spec,
        subtitle_spec=subtitle_spec,
        subtitles_enabled=subtitles_enabled,
    ) if execute else {
        'ok': None,
        'skipped': True,
        'reason': 'execute=false',
    }
    validators.setdefault('grades', {})
    validators.setdefault('issues', [])
    if execute:
        if render_execution.get('ok') is True:
            validators['grades']['execution_grade'] = 'GREEN'
        else:
            validators['grades']['execution_grade'] = 'RED'
            validators['issues'].append(
                f"Executed render failed at {render_execution.get('stage', 'unknown')}: "
                f"{render_execution.get('error', 'unknown error')}"
            )
    elif render_execution.get('skipped'):
        validators['grades']['execution_grade'] = 'YELLOW'
    quality_rating = compute_quality(validators, fleet, convergence_passes)

    try:
        from .scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=7, fleet=fleet,
            stars=quality_rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=quality_rating.get('label', ''),
        )
    except Exception:
        pass

    try:
        from .skill_db import register_skill
        # Best-effort prompt key: subject if provided, else compositionId+scene names
        _key_parts = [subject] if subject else []
        if not _key_parts:
            _key_parts.append(str(scene_manifest.get('composition_id', '')))
            _key_parts.extend([s.get('name', '') for s in scene_manifest.get('scenes', [])[:6]])
        _key = ' '.join(p for p in _key_parts if p)[:500]
        _summary = f"step7 render · {(quality_rating.get('label') or '')[:80]}"
        _excerpt = {
            'output_file': render_spec.get('output_file'),
            'resolution': render_spec.get('resolution', '1920x1080'),
            'fps': render_spec.get('fps', 30),
            'subtitles_enabled': subtitles_enabled,
            'fleet_verdicts': fleet.get('verdicts', {}),
            'motion_clips': render_spec.get('motion_clips', {}),
        }
        register_skill(
            step=7, prompt=_key,
            stars=quality_rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=7, prompt=_key, summary=_summary,
                          result_excerpt=_excerpt, stars=quality_rating.get('stars', 0.0))
    except Exception:
        pass

    return {
        'hermes': hermes,
        'render_outline': outline,
        'render_spec': render_spec,
        'harvest': {
            'render_tools': harvest.get('render_tools', []),
            'remotion_info': harvest.get('remotion_info', {}),
            'github_refs': harvest.get('github_refs', []),
        },
        'validators': validators,
        'fleet': fleet,
        'quality_rating': quality_rating,
        'render_execution': render_execution,
        'asset_reports': asset_reports,
        'motion_clips': motion_clips_report,
        'convergence_passes': convergence_passes,
        'elapsed_seconds': round(time.time() - started, 1),
        'stage_times': stage_times,
        'subtitles_enabled': subtitles_enabled,
        'iteration': bool(prior_render_spec or notes),
        'mode': mode,
    }
