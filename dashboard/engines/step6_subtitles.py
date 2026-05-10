#!/usr/bin/env python3.13
"""Step 6 — Subtitles & Transcription engine.

Takes the locked audio spec (Step 5) + narration script (Step 2) and
produces a complete SUBTITLE PRODUCTION SPEC: SRT file, word-level timing,
per-scene caption blocks, FFmpeg burn-in command, and VTT export.

Primary tool: whisper.cpp (local, free). Fallback: ffmpeg silence detection.
Output feeds directly into Step 7 (Render & Compositing).

Pipeline (5 stages, same shape as Steps 1-5):
  Stage 1 — HERMES PRE-ROUTE     (audio → subtitle tool selection + style routing)
  Stage 2 — SUBTITLE OUTLINE     (word-level timing map, CPS budget, line break plan)
  Stage 3 — DRAFT SUBTITLE SPEC  (SRT blocks, VTT export, burn-in command)
  Stage 4 — VALIDATE             (char/line limits, scene coverage, CPS, alignment)
  Stage 5 — FLEET REVIEW         (readability / timing / accuracy / engagement)

Hard validators:
  • Max 42 chars per line
  • Max 2 lines per subtitle card
  • Max 17 chars per second (CPS — reading speed ceiling)
  • All 6 GDS scene sections have at least 1 subtitle card
  • Total subtitle blocks >= 8
  • No orphan words (single-word subtitle cards)
  • SRT index is consecutive from 1
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

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL  = os.environ.get('STEP6_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL   = os.environ.get('STEP6_DEEP_MODEL',  'sonar-pro')

MAX_CHARS_PER_LINE = 42
MAX_LINES_PER_CARD = 2
MAX_CPS            = 17.0   # characters per second — reading speed ceiling
MIN_BLOCK_DURATION = 0.8    # seconds — minimum card display time
GDS_SECTIONS       = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']


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

import urllib.request
import urllib.error


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

HERMES_TEMPLATE = """You are Hermes orchestrating a SUBTITLE & TRANSCRIPTION step. You have:
  - A locked narration audio spec (Step 5) with per-scene timing
  - A locked narration script (Step 2)
  - Available subtitle tools on this machine

Select the best subtitle generation approach and surface constraints
the subtitle spec MUST respect.

SCRIPT EXCERPT (first 300 chars):
{script_excerpt}

AUDIO SPEC SUMMARY (from Step 5):
{audio_summary}

AVAILABLE SUBTITLE TOOLS (detected on this machine):
{subtitle_tools}

RECENT LEARNINGS (from past Step 6 runs):
{learnings}

USER NOTES:
{notes}

Output VALID JSON ONLY:
{{
  "subtitle_engine":    "<whisper-cpp | whisper | ffmpeg-silence | manual>",
  "whisper_model":      "<tiny | base | small | medium — pick smallest that fits quality need>",
  "style_preset":       "<clean | bold | caption | lower-third>",
  "font_family":        "<e.g. Arial, Helvetica, Impact>",
  "font_size_px":       <int 28-48>,
  "position":           "<bottom-center | top-center | lower-third>",
  "background_box":     <true|false>,
  "text_color":         "<#hex>",
  "bg_color":           "<#hex with alpha e.g. #00000099>",
  "domain_style_note":  "<one sentence — e.g. 'bold white on dark for BTC content'>",
  "fleet_owner_hint":   "<Dexter | Memo | Sienna | Nano>",
  "stop_or_proceed":    "PROCEED|STOP",
  "stop_reason":        ""
}}
"""


def hermes_preroute(script: str, audio_spec: dict, notes: str = '',
                    subtitle_tools: list | None = None) -> dict:
    try:
        from .learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        learnings_text = '(learnings store unavailable)'

    audio_summary = {
        'tts_engine': audio_spec.get('tts_engine', 'piper'),
        'voice_model': audio_spec.get('voice_model', 'en_US-lessac-medium'),
        'output_file': audio_spec.get('output_file', 'narration_piper.wav'),
        'estimated_total_seconds': (audio_spec.get('master_assembly') or {}).get('estimated_total_seconds', 41.0),
        'segment_count': len(audio_spec.get('segments', [])),
    }

    tools_summary = json.dumps([t.get('name') for t in (subtitle_tools or [])], indent=2)

    payload = HERMES_TEMPLATE.format(
        script_excerpt=(script or '')[:300],
        audio_summary=json.dumps(audio_summary, indent=2)[:400],
        subtitle_tools=tools_summary or '(none detected)',
        learnings=learnings_text,
        notes=(notes or '(none)')[:600],
    )
    raw = _call_ollama(payload, timeout=120)
    spec = _extract_json(raw)
    if spec:
        spec.setdefault('stop_or_proceed', 'PROCEED')
        spec.setdefault('subtitle_engine', 'whisper-cpp')
        spec.setdefault('whisper_model', 'base')
        spec.setdefault('style_preset', 'clean')
        spec.setdefault('font_family', 'Arial')
        spec.setdefault('font_size_px', 36)
        spec.setdefault('position', 'bottom-center')
        spec.setdefault('background_box', True)
        spec.setdefault('text_color', '#FFFFFF')
        spec.setdefault('bg_color', '#00000099')
        return spec
    return {
        'subtitle_engine': 'whisper-cpp', 'whisper_model': 'base',
        'style_preset': 'clean', 'font_family': 'Arial',
        'font_size_px': 36, 'position': 'bottom-center',
        'background_box': True, 'text_color': '#FFFFFF', 'bg_color': '#00000099',
        'domain_style_note': 'clean white captions on dark background for BTC content',
        'fleet_owner_hint': 'Dexter',
        'stop_or_proceed': 'PROCEED', 'stop_reason': '',
    }


# ---------------------------------------------------------------------------
# Stage 2 — Subtitle outline
# ---------------------------------------------------------------------------

OUTLINE_TEMPLATE = """You are a subtitle editor. Create a word-level timing plan that will
produce a clean, readable subtitle file for a 41-second crypto video.

SCRIPT (full — this is what gets spoken):
{script}

AUDIO TIMING (per-scene from Step 5):
{audio_timing}

SUBTITLE STYLE:
{style}

Rules:
- Max {max_chars} characters per line
- Max {max_lines} lines per subtitle card
- Max {max_cps} characters per second (reading speed)
- Min {min_dur}s display time per card
- Group sentences naturally — break at commas, conjunctions, or natural pauses
- Map each group to its GDS scene: hook, thesis, evidence_1, evidence_2, implication, cta
- Time codes must be sequential and non-overlapping
- Target ~{target_blocks} subtitle cards total for a 41s video

Output VALID JSON ONLY:
{{
  "total_blocks": <int>,
  "total_duration_seconds": <float>,
  "scene_coverage": {{"hook": <int blocks>, "thesis": <int>, "evidence_1": <int>, "evidence_2": <int>, "implication": <int>, "cta": <int>}},
  "blocks": [
    {{
      "index": 1,
      "scene_id": "hook",
      "start_time": "00:00:00,000",
      "end_time": "00:00:02,500",
      "start_seconds": 0.0,
      "end_seconds": 2.5,
      "duration_seconds": 2.5,
      "lines": ["First line of text", "Second line if needed"],
      "char_count": <int>,
      "cps": <float>
    }}
  ]
}}
"""


def subtitle_outline(hermes: dict, script: str, audio_spec: dict) -> dict:
    segments = audio_spec.get('segments', [])
    audio_timing = {s.get('scene_id', f'seg{i}'): s.get('estimated_seconds', 6)
                    for i, s in enumerate(segments)}

    payload = OUTLINE_TEMPLATE.format(
        script=(script or '')[:3000],
        audio_timing=json.dumps(audio_timing, indent=2)[:500],
        style=json.dumps({
            'font': hermes.get('font_family'), 'size': hermes.get('font_size_px'),
            'position': hermes.get('position'), 'preset': hermes.get('style_preset'),
        }, indent=2),
        max_chars=MAX_CHARS_PER_LINE,
        max_lines=MAX_LINES_PER_CARD,
        max_cps=MAX_CPS,
        min_dur=MIN_BLOCK_DURATION,
        target_blocks=14,
    )
    raw = _call_ollama(payload, timeout=180)
    result = _extract_json(raw) or {}
    # Ensure sequential indices
    blocks = result.get('blocks', [])
    for i, b in enumerate(blocks):
        b['index'] = i + 1
    result['blocks'] = blocks
    return result


# ---------------------------------------------------------------------------
# Stage 3 — Draft subtitle spec
# ---------------------------------------------------------------------------

SPEC_TEMPLATE = """You are a subtitle engineer. Produce the complete subtitle production spec
including the SRT file content, VTT content, whisper command, and FFmpeg burn-in command.

HERMES STYLE SETTINGS:
{hermes}

SUBTITLE OUTLINE (use these exact blocks and timecodes):
{outline}

AUDIO FILE: {audio_file}
VIDEO FILE (from Step 4): video_raw.mp4
TARGET SUBTITLE LANGUAGE: {language} (ISO 639-1 code; "en" = English).
  If non-English, write `srt_content` and `vtt_content` IN THAT LANGUAGE (translate the
  script's wording while preserving the timecodes from the outline exactly). The audio is
  in English; subtitles can be in a different language for international viewers.

Output VALID JSON ONLY:
{{
  "srt_content": "<complete SRT file as a string — use \\n for newlines within JSON>",
  "vtt_content": "<complete WebVTT file as a string>",
  "whisper_command": "whisper-cpp --model base.en --output-srt --file narration_piper.wav",
  "ffmpeg_burnin": "ffmpeg -i video_raw.mp4 -vf \\"subtitles=subtitles.srt:force_style='...'\\",scale=1920:1080 -c:a copy output_with_subs.mp4",
  "ffmpeg_add_audio": "ffmpeg -i video_raw.mp4 -i narration_piper.wav -c:v copy -c:a aac -b:a 192k -shortest video_with_audio.mp4",
  "output_srt_file": "subtitles.srt",
  "output_vtt_file": "subtitles.vtt",
  "style_ffmpeg_args": "FontName={font},FontSize={font_size},PrimaryColour=&H{text_color},BackColour=&H{bg_color},BorderStyle=3",
  "scene_map": {{
    "hook": {{"start_block": 1, "end_block": 3}},
    "thesis": {{"start_block": 4, "end_block": 6}},
    "evidence_1": {{"start_block": 7, "end_block": 9}},
    "evidence_2": {{"start_block": 10, "end_block": 12}},
    "implication": {{"start_block": 13, "end_block": 14}},
    "cta": {{"start_block": 15, "end_block": 16}}
  }},
  "qa_notes": ["<any subtitle quality risks or items needing manual check>"]
}}
"""


def draft_subtitle_spec(hermes: dict, outline: dict, script: str,
                        mode: str = 'fast', harvest: dict | None = None,
                        language: str = 'en') -> dict:
    harvest = harvest or {}
    audio_file = 'narration_piper.wav'

    payload = SPEC_TEMPLATE.format(
        hermes=json.dumps(hermes, indent=2)[:600],
        outline=json.dumps({
            'total_blocks': outline.get('total_blocks', 0),
            'blocks': outline.get('blocks', [])[:20],
        }, indent=2)[:3000],
        audio_file=audio_file,
        font=hermes.get('font_family', 'Arial'),
        font_size=hermes.get('font_size_px', 36),
        text_color=hermes.get('text_color', '#FFFFFF').lstrip('#'),
        bg_color=hermes.get('bg_color', '#00000099').lstrip('#'),
        language=language,
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

def _parse_srt_blocks(srt: str) -> list[dict]:
    """Parse SRT string into list of dicts with index, start, end, text."""
    blocks = []
    for chunk in re.split(r'\n\n+', (srt or '').strip()):
        lines = [l for l in chunk.strip().splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0])
            timing = lines[1].split(' --> ')
            text = '\n'.join(lines[2:])
            blocks.append({'index': idx, 'start': timing[0].strip(), 'end': timing[1].strip(), 'text': text})
        except Exception:
            continue
    return blocks


def _srt_to_seconds(ts: str) -> float:
    """Convert SRT timestamp HH:MM:SS,mmm to float seconds."""
    try:
        ts = ts.replace(',', '.')
        parts = ts.split(':')
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        return 0.0


def validate_subtitles(spec: dict, outline: dict) -> dict:
    grades: dict = {}
    issues: list = []

    srt = spec.get('srt_content', '')
    blocks = _parse_srt_blocks(srt)
    outline_blocks = outline.get('blocks', [])

    # Block count
    block_count = len(blocks) or len(outline_blocks)
    grades['block_count_grade'] = 'GREEN' if block_count >= 8 else ('YELLOW' if block_count >= 5 else 'RED')
    if block_count < 8:
        issues.append(f'Only {block_count} subtitle blocks (min 8 required)')

    # Character limit per line
    char_violations = []
    for b in outline_blocks:
        for line in (b.get('lines') or []):
            if len(line) > MAX_CHARS_PER_LINE:
                char_violations.append(f'Block {b.get("index","?")}: "{line[:30]}…" ({len(line)} chars)')
    grades['char_limit_grade'] = 'GREEN' if not char_violations else ('YELLOW' if len(char_violations) <= 2 else 'RED')
    if char_violations:
        issues.append(f'Lines exceed {MAX_CHARS_PER_LINE} chars: {char_violations[:2]}')

    # Line count per card
    line_violations = [b for b in outline_blocks if len(b.get('lines', [])) > MAX_LINES_PER_CARD]
    grades['line_count_grade'] = 'GREEN' if not line_violations else 'YELLOW'
    if line_violations:
        issues.append(f'{len(line_violations)} card(s) exceed {MAX_LINES_PER_CARD} lines')

    # CPS check
    cps_violations = [b for b in outline_blocks
                      if b.get('cps', 0) > MAX_CPS and b.get('cps', 0) > 0]
    grades['cps_grade'] = 'GREEN' if not cps_violations else ('YELLOW' if len(cps_violations) <= 1 else 'RED')
    if cps_violations:
        issues.append(f'{len(cps_violations)} block(s) exceed {MAX_CPS} CPS reading speed')

    # Scene coverage
    scene_coverage = outline.get('scene_coverage', {})
    covered = [s for s in GDS_SECTIONS if scene_coverage.get(s, 0) > 0]
    missing = [s for s in GDS_SECTIONS if scene_coverage.get(s, 0) == 0]
    grades['coverage_grade'] = 'GREEN' if not missing else ('YELLOW' if len(missing) <= 1 else 'RED')
    if missing:
        issues.append(f'Missing subtitle coverage for: {", ".join(missing)}')

    # SRT index consecutiveness
    idx_ok = all(blocks[i]['index'] == i + 1 for i in range(len(blocks))) if blocks else True
    grades['index_grade'] = 'GREEN' if idx_ok else 'YELLOW'
    if not idx_ok:
        issues.append('SRT block indices are not consecutive')

    # FFmpeg burn-in command present
    has_burnin = bool(spec.get('ffmpeg_buryin') or spec.get('ffmpeg_burni') or spec.get('ffmpeg_burni'))
    # check any key containing ffmpeg
    has_buryin = any('ffmpeg' in str(v) for v in spec.values() if isinstance(v, str))
    grades['ffmpeg_grade'] = 'GREEN' if has_buryin else 'YELLOW'
    if not has_buryin:
        issues.append('ffmpeg_buryin command missing from spec')

    # Orphan words (single-word cards)
    orphans = [b for b in outline_blocks
               if sum(len(l.split()) for l in b.get('lines', [])) == 1]
    grades['orphan_grade'] = 'GREEN' if not orphans else 'YELLOW'
    if orphans:
        issues.append(f'{len(orphans)} single-word subtitle card(s) found')

    overall_reds = sum(1 for g in grades.values() if g == 'RED')
    overall_yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    return {
        'grades': grades, 'issues': issues,
        'overall_reds': overall_reds, 'overall_yellows': overall_yellows,
        'block_count': block_count,
        'char_violations': len(char_violations),
        'cps_violations': len(cps_violations),
        'scene_coverage': scene_coverage,
        'covered_scenes': len(covered),
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Dev — subtitle pipeline feasibility reviewer',
        'lens': 'Can the SRT file be parsed correctly by ffmpeg? Check the ffmpeg_buryin command syntax. Verify the subtitle filter args, scale flag, and output file. Flag any command that would fail at render time.',
    },
    'memo': {
        'role': 'PM — subtitle timing and sync reviewer',
        'lens': 'Do subtitle timecodes align with the audio prosody from Step 5? Check that each scene transition in the subtitle outline matches the audio segment boundaries ± 0.5s. Flag any block that visually lags or leads by more than 1 second.',
    },
    'sienna': {
        'role': 'Domain Specialist — subtitle accuracy and terminology reviewer',
        'lens': 'Are technical terms like BTC, DeFi, blockchain spelled correctly in the subtitle text? Are numbers written consistently (e.g. "$47K" vs "forty-seven thousand")? Flag any text that differs from the script or would confuse a reader.',
    },
    'nano': {
        'role': 'Agent Creator — readability and engagement reviewer',
        'lens': 'Are subtitle line breaks placed at natural language boundaries? Are cards long enough to read comfortably (min 0.8s)? Does the pacing feel smooth when reading along? Flag any card that feels rushed, choppy, or awkwardly split mid-phrase.',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent} ({role}).

Review this subtitle production spec. Focus ONLY on your lens:
{lens}

SUBTITLE SPEC SUMMARY:
{spec_summary}

VALIDATORS:
{validators}

Output Markdown ONLY with these exact sections:
### {agent_cap} — what to fix
(2-4 sharp bullets. Be specific about which block and what to change.)

### {agent_cap} — verdict
GREEN-LIGHT | YELLOW-LIGHT | RED-LIGHT — one sentence reason.

### {agent_cap} — if I owned this
(1 concrete first move you would make.)
"""


def _review_one(agent: str, cfg: dict, spec: dict, outline: dict, validators: dict) -> tuple[str, str]:
    summary = json.dumps({
        'subtitle_engine': spec.get('subtitle_engine'),
        'whisper_model': spec.get('whisper_model'),
        'total_blocks': outline.get('total_blocks', 0),
        'scene_coverage': outline.get('scene_coverage', {}),
        'style': {
            'font': spec.get('font_family'), 'size': spec.get('font_size_px'),
            'position': spec.get('position'), 'bg': spec.get('background_box'),
        },
        'has_srt': bool(spec.get('srt_content')),
        'has_ffmpeg_buryin': any('ffmpeg' in str(v) for v in spec.values() if isinstance(v, str)),
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
        label = 'Production-ready — advance to Render'
    elif stars >= 4.0:
        label = 'Strong — refine to 5★ before Render'
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
# Harvest — installed subtitle + transcription tools + OSS registry
# ---------------------------------------------------------------------------

def harvest_subtitle_tools() -> list[dict]:
    tools = [
        ('whisper-cpp',    'transcription', 'Whisper.cpp — fast local transcription + SRT export'),
        ('whisper',        'transcription', 'OpenAI Whisper (Python) — transcription'),
        ('ffmpeg',         'processing',    'FFmpeg — subtitle burn-in, audio mux, encoding'),
        ('ffprobe',        'analysis',      'FFprobe — audio/video stream info'),
        ('subsync',        'sync',          'SubSync — automatic subtitle synchronization'),
        ('aegisub',        'editing',       'Aegisub — subtitle editor (manual review)'),
        ('ccextractor',    'extraction',    'CCExtractor — closed caption extraction'),
        ('srt-tools',      'processing',    'SRT tools — subtitle format conversion'),
    ]
    out = []
    for bin_name, category, desc in tools:
        path = shutil.which(bin_name) or ''
        if path:
            out.append({'name': bin_name, 'category': category, 'description': desc, 'path': path})
    return out


def harvest_whisper_models() -> list[str]:
    """Find locally downloaded whisper models."""
    search_dirs = [
        Path.home() / '.cache' / 'whisper',
        Path.home() / 'whisper',
        Path('/usr/share/whisper'),
        Path.home() / 'whisper-cpp' / 'models',
        Path('/opt/whisper/models'),
    ]
    models = []
    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix in ('.bin', '.pt', '.onnx') and f.is_file():
                models.append(f.name)
    return sorted(set(models))[:10]


def harvest_subtitle_github_refs() -> list[dict]:
    try:
        result = subprocess.run(
            ['gh', 'search', 'repos', 'whisper subtitle srt generator auto captions video',
             '--sort', 'stars', '--limit', '6',
             '--json', 'nameWithOwner,description,stargazerCount,url'],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout or '[]')
    except Exception:
        return []


def harvest_oss_registry_subtitles() -> str:
    try:
        from .discovery import registry_for_steps
        return registry_for_steps(steps=['step6_subtitles'], categories=['editing', 'quality'], max_tools=12)
    except Exception:
        return '(OSS registry unavailable)'


def harvest_step6(hermes: dict) -> dict:
    out: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(harvest_subtitle_tools): 'subtitle_tools',
            pool.submit(harvest_whisper_models): 'whisper_models',
            pool.submit(harvest_subtitle_github_refs): 'github_refs',
            pool.submit(harvest_oss_registry_subtitles): 'oss_registry',
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

REWRITE_TEMPLATE = """The subtitle spec has RED-LIGHT critiques. Rewrite it to fix them.
Output the corrected FULL subtitle spec JSON (same schema).

CURRENT SPEC:
{spec}

RED-LIGHT CRITIQUES:
{critiques}

VALIDATOR ISSUES:
{issues}

Output VALID JSON ONLY — full corrected spec:
"""


def _rewrite_spec(spec: dict, outline: dict, fleet: dict, validators: dict) -> tuple[dict, dict]:
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
    new_spec = _extract_json(raw) or spec
    return new_spec, outline


# ---------------------------------------------------------------------------
# Post-research learnings
# ---------------------------------------------------------------------------

def step6_post_research(result: dict, user_notes: str = '') -> dict:
    spec = result.get('subtitle_spec') or {}
    validators = result.get('validators') or {}
    rating = result.get('quality_rating') or {}

    prompt = f"""Extract concise learnings from this Step 6 subtitle run.

SUBTITLE ENGINE: {spec.get('subtitle_engine')} / whisper model: {spec.get('whisper_model')}
VALIDATORS: {json.dumps(validators.get('grades', {}), indent=2)[:300]}
QUALITY: {rating.get('stars')}★ — {rating.get('label')}
USER NOTES: {user_notes or '(none)'}

Output VALID JSON ONLY:
{{
  "what_worked": ["<1-3 subtitle/timing patterns that scored well>"],
  "what_failed": ["<1-3 patterns that caused RED verdicts>"],
  "style_lessons": ["<font, position, line-break lessons>"],
  "timing_lessons": ["<timecode alignment lessons>"],
  "tool_lessons": ["<whisper/ffmpeg command lessons>"],
  "next_video_recommendations": ["<1-2 recommendations for next run>"]
}}"""

    raw = _call_ollama(prompt, timeout=90)
    record: dict = _extract_json(raw) or {}
    record.update({
        'kind': 'step6_advance',
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

def step6_advise(result: dict) -> dict:
    validators = result.get('validators') or {}
    fleet = result.get('fleet') or {}
    rating = result.get('quality_rating') or {}
    stars = rating.get('stars', 3.0)

    prompt = f"""A Step 6 subtitle spec scored {stars}★. Diagnose the top issue and write focused
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
    return _extract_json(raw) or {'diagnosis': 'Unknown issue', 'focused_notes': 'Review subtitle timing and line breaks.'}


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

# Spec: "subtitle in the corresponding language" — multi-language support.
SUPPORTED_LANGUAGES = {
    'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German',
    'it': 'Italian', 'pt': 'Portuguese', 'ro': 'Romanian',
    'pl': 'Polish', 'nl': 'Dutch', 'tr': 'Turkish',
    'ru': 'Russian', 'uk': 'Ukrainian',
    'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
    'ar': 'Arabic', 'hi': 'Hindi',
}


def _normalise_language(language: str) -> str:
    """Coerce input to a supported ISO code; default to 'en' if unknown."""
    raw = (language or 'en').strip().lower()
    if raw in SUPPORTED_LANGUAGES:
        return raw
    # Accept full names like "Spanish"
    for c, name in SUPPORTED_LANGUAGES.items():
        if raw == name.lower():
            return c
    # Tolerate locales like 'en-US' or 'es_MX' by taking the prefix
    head = raw.split('-')[0].split('_')[0]
    if head in SUPPORTED_LANGUAGES:
        return head
    return 'en'


def _apply_language_to_spec(spec: dict, language: str) -> dict:
    """Post-process the LLM-drafted spec to honour the target language.

    - Sets `spec.language` for downstream consumers.
    - Adds `--language <code>` to the whisper command (forces detection to that lang).
    - Suffixes output filenames with the language code for non-English (e.g.
      `subtitles.es.srt`) so multi-language renders don't overwrite each other.
    - No-op for 'en' beyond setting the language field — preserves legacy filenames.
    """
    code = _normalise_language(language)
    spec['language']      = code
    spec['language_name'] = SUPPORTED_LANGUAGES.get(code, code)

    cmd = spec.get('whisper_command') or ''
    if cmd and '--language' not in cmd:
        # Insert --language flag before the --file/--output args
        spec['whisper_command'] = re.sub(r'\b(whisper(?:-cpp)?)\b', r'\1 --language ' + code, cmd, count=1)

    if code != 'en':
        for key in ('output_srt_file', 'output_vtt_file'):
            v = spec.get(key) or ''
            if v and f'.{code}.' not in v:
                # subtitles.srt -> subtitles.es.srt
                if v.endswith('.srt'):
                    spec[key] = v[:-4] + f'.{code}.srt'
                elif v.endswith('.vtt'):
                    spec[key] = v[:-4] + f'.{code}.vtt'
        # Update ffmpeg_burnin to point at the renamed SRT
        burn = spec.get('ffmpeg_burnin') or ''
        new_srt = spec.get('output_srt_file') or ''
        if burn and new_srt and 'subtitles.srt' in burn:
            spec['ffmpeg_burnin'] = burn.replace('subtitles.srt', new_srt)

    return spec


def run_step6(script: str = '', audio_spec: dict | None = None,
              mode: str = 'fast', notes: str = '',
              prior_subtitle_spec: dict | None = None,
              max_convergence: int = 2,
              project: str = 'default',
              language: str = 'en') -> dict:
    started = time.time()
    stage_times: dict = {}
    audio_spec = audio_spec or {}

    # Stage 1: Hermes (needs subtitle tools for engine selection)
    t = time.time()
    subtitle_tools_quick = harvest_subtitle_tools()
    hermes = hermes_preroute(script=script, audio_spec=audio_spec,
                             notes=notes, subtitle_tools=subtitle_tools_quick)
    stage_times['hermes_preroute'] = round(time.time() - t, 1)
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'stopped': True, 'hermes': hermes,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # Stage 2a + 2b: Subtitle outline + full harvest in parallel
    t = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outline_fut = pool.submit(subtitle_outline, hermes, script, audio_spec)
        harvest_fut = pool.submit(harvest_step6, hermes)
        outline = outline_fut.result(timeout=200)
        harvest  = harvest_fut.result(timeout=35)
    stage_times['outline_and_harvest'] = round(time.time() - t, 1)

    # Normalise language now so it flows through draft + post-process.
    language = _normalise_language(language)

    # Stage 3: Draft subtitle spec
    t = time.time()
    subtitle_spec = draft_subtitle_spec(hermes=hermes, outline=outline,
                                        script=script, mode=mode, harvest=harvest,
                                        language=language)
    stage_times['draft_subtitle_spec'] = round(time.time() - t, 1)

    # Stage 4: Validate
    t = time.time()
    validators = validate_subtitles(subtitle_spec, outline)
    stage_times['validate'] = round(time.time() - t, 1)

    # Stage 5: Fleet review + convergence loop
    convergence_passes = 0
    while convergence_passes <= max_convergence:
        t = time.time()
        fleet = fleet_review(subtitle_spec, outline, validators)
        stage_times[f'fleet_review_pass_{convergence_passes + 1}'] = round(time.time() - t, 1)

        red_count = fleet.get('verdicts', {}).get('RED', 0)
        if red_count > 0 and convergence_passes < max_convergence:
            t = time.time()
            subtitle_spec, outline = _rewrite_spec(subtitle_spec, outline, fleet, validators)
            validators = validate_subtitles(subtitle_spec, outline)
            convergence_passes += 1
            stage_times[f'rewrite_pass_{convergence_passes}'] = round(time.time() - t, 1)
        else:
            break

    quality_rating = compute_quality(validators, fleet, convergence_passes)

    # Spec: subtitle in user's chosen language. Adds whisper --language flag,
    # renames output files for non-English, and stamps spec.language for downstream.
    subtitle_spec = _apply_language_to_spec(subtitle_spec, language=language)

    try:
        from .scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=6, fleet=fleet,
            stars=quality_rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=quality_rating.get('label', ''),
        )
    except Exception:
        pass

    try:
        from .skill_db import register_skill
        _prompt = (script or '')[:500]
        _summary = f"step6 subtitles · {(quality_rating.get('label') or '')[:80]}"
        _excerpt = {
            'subtitle_count': len(subtitle_spec.get('srt_blocks', []) or subtitle_spec.get('blocks', [])),
            'engine': subtitle_spec.get('engine') or subtitle_spec.get('tool'),
            'fleet_verdicts': fleet.get('verdicts', {}),
        }
        register_skill(
            step=6, prompt=_prompt,
            stars=quality_rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=6, prompt=_prompt, summary=_summary,
                          result_excerpt=_excerpt, stars=quality_rating.get('stars', 0.0))
    except Exception:
        pass

    return {
        'hermes': hermes,
        'subtitle_outline': outline,
        'subtitle_spec': subtitle_spec,
        'harvest': {
            'subtitle_tools': harvest.get('subtitle_tools', []),
            'whisper_models': harvest.get('whisper_models', []),
            'github_refs': harvest.get('github_refs', []),
        },
        'validators': validators,
        'fleet': fleet,
        'quality_rating': quality_rating,
        'convergence_passes': convergence_passes,
        'elapsed_seconds': round(time.time() - started, 1),
        'stage_times': stage_times,
        'iteration': bool(prior_subtitle_spec or notes),
        'mode': mode,
    }
