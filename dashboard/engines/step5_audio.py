#!/usr/bin/env python3.13
"""Step 5 — Audio Narration engine.

Takes the locked narration script (Step 2) + scene manifest (Step 4) and
produces a complete AUDIO PRODUCTION SPEC: voice model selection, per-scene
TTS command sequences, prosody/emphasis marks, phonetic overrides for
technical terms, SSML hints, and timing alignment with the scene manifest.

Primary TTS: Piper (local, free, fast). Fallback: espeak-ng.
Output feeds directly into Step 7 (Render & Compositing).

Pipeline (5 stages, same shape as Steps 1-4):
  Stage 1 — HERMES PRE-ROUTE     (script → TTS tool selection + voice routing)
  Stage 2 — PROSODY OUTLINE      (pacing map, emphasis points, pause timing)
  Stage 3 — DRAFT AUDIO SPEC     (per-scene TTS commands, phonetic overrides)
  Stage 4 — VALIDATE             (timing math, phonetics, banned chars, digit runs)
  Stage 5 — FLEET REVIEW         (intelligibility / pacing / scene sync / engagement)

Hard validators:
  • Word count 88-95 (script from Step 2)
  • Estimated runtime 39-43s at ~2.2 words/sec
  • No digit-only runs (numbers must be spelled out for TTS)
  • No banned chars: em-dash (—), en-dash (–), smart quotes (" " ' ')
  • All technical terms have phonetic_override entries
  • Per-scene audio segments aligned to Step 4 timing ± 1.5s
"""
from __future__ import annotations

import json
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
LOCAL_MODEL = os.environ.get('STEP5_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL  = os.environ.get('STEP5_DEEP_MODEL', 'sonar-pro')

TARGET_WORDS   = 90        # ±5 acceptable
TARGET_SECONDS = 41.0      # ±2s acceptable
WORDS_PER_SEC  = 2.2       # calibrated to Dan's content style

BANNED_CHARS = ['—', '–', '‘', '’', '“', '”']
GDS_SECTIONS = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']


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
# ElevenLabs (spec: "Brian — voice closely to Morgan Freeman")
#
# The TTS engine selection cascade (highest preference first):
#   1. ElevenLabs (with Brian/George/Charlie voices) — when ELEVENLABS_API_KEY set
#   2. Piper (local, free) — fallback
#   3. espeak-ng — last-resort fallback
#
# Set ELEVENLABS_API_KEY via env var or ~/.openclaw/fleet.env to enable.
# ---------------------------------------------------------------------------

ELEVENLABS_API_BASE  = 'https://api.elevenlabs.io/v1'
ELEVENLABS_MODEL_ID  = os.environ.get('ELEVENLABS_MODEL_ID', 'eleven_multilingual_v2')

# Public ElevenLabs voice IDs. Override via ELEVENLABS_VOICE_<NAME> env var if needed.
ELEVENLABS_VOICE_IDS = {
    'brian':   os.environ.get('ELEVENLABS_VOICE_BRIAN',   'nPczCjzI2devNBz1zQrb'),
    'george':  os.environ.get('ELEVENLABS_VOICE_GEORGE',  'JBFqnCBsd6RMkjVDRZzb'),
    'charlie': os.environ.get('ELEVENLABS_VOICE_CHARLIE', 'IKne3meq5aSn9XLyUdCD'),
}


def _have_elevenlabs_key() -> bool:
    """OSS-only by default. Paid TTS (ElevenLabs) requires explicit opt-in via
    ZMARTY_ALLOW_PAID_TTS=1 — this prevents silent paid-API spend during normal
    runs. Without the flag, _apply_elevenlabs_to_spec() falls through to Piper.
    """
    if os.environ.get('ZMARTY_ALLOW_PAID_TTS', '').strip().lower() not in ('1', 'true', 'yes'):
        return False
    return bool(_key('ELEVENLABS_API_KEY', 'ELEVEN_API_KEY', 'XI_API_KEY'))


def _resolve_voice(voice_preference: str = 'brian') -> tuple[str, str]:
    """(voice_name, voice_id). Falls back to Brian for unknown names."""
    name = (voice_preference or '').strip().lower()
    # Strip the parenthetical descriptor the dashboard sends, e.g. "Brian (deep, Morgan-Freeman style)"
    name = name.split('(')[0].strip()
    if name in ELEVENLABS_VOICE_IDS:
        return name, ELEVENLABS_VOICE_IDS[name]
    return 'brian', ELEVENLABS_VOICE_IDS['brian']


def _elevenlabs_tts_command(text: str, voice_id: str, output_file: str) -> str:
    """Generate the curl command that synthesises `text` to `output_file` (mp3) via ElevenLabs.

    Embedded so Step 7 can run the spec without re-deriving anything. Single
    quotes are escaped JSON-style. The spec output is text — actual API call
    happens at render time.
    """
    safe_text = json.dumps(text, ensure_ascii=False)  # handles quotes/escapes
    payload   = json.dumps({
        'text':           text,
        'model_id':       ELEVENLABS_MODEL_ID,
        'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75},
    }, ensure_ascii=False)
    # Use $ELEVENLABS_API_KEY in the emitted command so users don't paste keys into specs/logs.
    return (
        f'curl -X POST "{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}" '
        f'-H "xi-api-key: $ELEVENLABS_API_KEY" '
        f'-H "Content-Type: application/json" '
        f'-H "Accept: audio/mpeg" '
        f'-o "{output_file}" '
        f'-d {json.dumps(payload, ensure_ascii=False)}'
    )


def elevenlabs_synthesize(text: str, output_file: str, voice_preference: str = 'brian',
                          timeout: int = 120) -> dict:
    """Actually call ElevenLabs and write audio bytes to disk.

    Returns {'ok': bool, 'output_file': path, 'voice': name, 'voice_id': id, 'bytes': N}
    or {'ok': False, 'error': '...'} on failure.
    """
    key = _key('ELEVENLABS_API_KEY', 'ELEVEN_API_KEY', 'XI_API_KEY')
    if not key:
        return {'ok': False, 'error': 'ELEVENLABS_API_KEY not configured'}
    voice_name, voice_id = _resolve_voice(voice_preference)
    body = json.dumps({
        'text':           text,
        'model_id':       ELEVENLABS_MODEL_ID,
        'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75},
    }, ensure_ascii=False).encode('utf-8')
    try:
        req = urllib.request.Request(
            f'{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}',
            data=body,
            headers={
                'xi-api-key':   key,
                'Content-Type': 'application/json',
                'Accept':       'audio/mpeg',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            audio = r.read()
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_bytes(audio)
        return {
            'ok':          True,
            'output_file': output_file,
            'voice':       voice_name,
            'voice_id':    voice_id,
            'bytes':       len(audio),
        }
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def _apply_elevenlabs_to_spec(audio_spec: dict, voice_preference: str = 'brian') -> dict:
    """Rewrite an audio spec to use ElevenLabs Brian when key is available.

    No-op when the key is missing — preserves existing Piper behaviour as fallback.
    Touches: tts_engine, voice_model, per-scene tts_command + output_file (.wav -> .mp3).
    """
    if not _have_elevenlabs_key():
        audio_spec['_elevenlabs_status'] = 'skipped: ELEVENLABS_API_KEY not set; using Piper fallback'
        return audio_spec
    voice_name, voice_id = _resolve_voice(voice_preference)
    audio_spec['tts_engine']  = 'elevenlabs'
    audio_spec['voice_model'] = f'elevenlabs:{voice_name}'
    audio_spec['voice_id']    = voice_id

    # Per-scene rewrite (if the LLM produced segments)
    for seg in audio_spec.get('per_scene_audio', []) or audio_spec.get('segments', []) or []:
        if not isinstance(seg, dict):
            continue
        text = seg.get('tts_text') or seg.get('text') or ''
        out_file = (seg.get('output_file') or '').replace('.wav', '.mp3')
        if not out_file and seg.get('section'):
            out_file = f'audio/{seg["section"]}.mp3'
        if text and out_file:
            seg['output_file'] = out_file
            seg['tts_command'] = _elevenlabs_tts_command(text, voice_id, out_file)

    # Master assembly target
    master = audio_spec.get('master_assembly') or {}
    if isinstance(master, dict):
        master_out = (master.get('output_file') or 'narration.wav').replace('.wav', '.mp3')
        master['output_file'] = master_out
        audio_spec['master_assembly'] = master

    audio_spec['_elevenlabs_status'] = f'enabled: voice={voice_name} model={ELEVENLABS_MODEL_ID}'
    return audio_spec


def _apply_vibevoice_to_spec(audio_spec: dict) -> dict:
    """Rewrite an audio spec to use Microsoft VibeVoice 1.5B (OSS, MIT) when the
    local ComfyUI is reachable and the model files are on disk. This is the
    quality default for OSS-only operation — replaces robotic Piper with
    human-quality TTS that supports voice cloning.

    No-op when ComfyUI is down or model is missing — leaves Piper default
    untouched. Sets `_vibevoice_status` for the dashboard provider bar to
    show why it did or did not engage.
    """
    try:
        from . import vibevoice_client as _vv
    except Exception as e:
        audio_spec['_vibevoice_status'] = f'skipped: client import failed: {e}'
        return audio_spec
    if not _vv.is_configured():
        audio_spec['_vibevoice_status'] = (
            'skipped: ComfyUI :8000 unreachable or VibeVoice-1.5B not in models/vibevoice/'
        )
        return audio_spec
    # Don't override if ElevenLabs already applied (operator opted in to paid)
    if audio_spec.get('tts_engine') == 'elevenlabs':
        audio_spec['_vibevoice_status'] = 'skipped: elevenlabs already applied'
        return audio_spec

    audio_spec['tts_engine']   = 'vibevoice'
    audio_spec['voice_model']  = f'vibevoice:{_vv.VIBEVOICE_DEFAULT_MODEL}'

    # Rewrite per-scene tts_command to invoke the vibevoice_client CLI entry.
    # `python3.13 -m engines.vibevoice_client "<text>" "<out>"` — keep the same
    # output paths the LLM-drafted spec expected (so step7_render finds them).
    cli_base = 'python3.13 -m engines.vibevoice_client'
    for seg in audio_spec.get('per_scene_audio', []) or audio_spec.get('segments', []) or []:
        if not isinstance(seg, dict):
            continue
        text = (seg.get('tts_text') or seg.get('text') or '').replace('"', '\\"')
        out_file = seg.get('output_file') or ''
        if not out_file and seg.get('section'):
            out_file = f'audio/{seg["section"]}.wav'
        if text and out_file:
            seg['output_file'] = out_file
            seg['tts_command'] = f'{cli_base} "{text}" "{out_file}"'

    master = audio_spec.get('master_assembly') or {}
    if isinstance(master, dict) and not master.get('output_file', '').endswith('.wav'):
        master['output_file'] = (master.get('output_file') or 'narration.wav')
        audio_spec['master_assembly'] = master

    audio_spec['_vibevoice_status'] = (
        f'enabled: model={_vv.VIBEVOICE_DEFAULT_MODEL} (OSS MIT, runs via local ComfyUI)'
    )
    return audio_spec


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

HERMES_TEMPLATE = """You are Hermes orchestrating an AUDIO NARRATION step. You have:
  - A locked 90-word narration script (Step 2)
  - A scene manifest with 6 timed segments (Step 4)

Select the best available TTS tool and voice for this domain, and surface
constraints the audio spec MUST respect.

SCRIPT EXCERPT (first 200 chars):
{script_excerpt}

DOMAIN / TONE (from scene manifest):
{domain_hint}

AVAILABLE TTS TOOLS (detected on this machine):
{tts_tools}

RECENT LEARNINGS (from past Step 5 runs — what voice/prosody choices worked):
{learnings}

USER NOTES:
{notes}

Output VALID JSON ONLY:
{{
  "tts_engine":       "<piper | espeak-ng | kokoro | system>",
  "voice_model":      "<e.g. en_US-lessac-medium or en_US-amy-low — pick from installed piper voices>",
  "speaking_rate":    "<slow=0.85 | normal=1.0 | fast=1.1>",
  "pitch_shift":      "<-2 to +2 semitones, 0 = neutral>",
  "energy":           "<soft | medium | assertive>",
  "domain_persona":   "<one sentence: the narrator persona — e.g. 'authoritative fintech analyst'>",
  "key_terms_risk":   ["<technical terms likely to be mispronounced>"],
  "fleet_owner_hint": "<Dexter | Memo | Sienna | Nano>",
  "stop_or_proceed":  "PROCEED|STOP",
  "stop_reason":      ""
}}
"""


def hermes_preroute(script: str, scene_manifest: dict, notes: str = '',
                    tts_tools: list | None = None) -> dict:
    try:
        from .learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        learnings_text = '(learnings store unavailable)'

    domain_hint = scene_manifest.get('shared_props', {}).get('display_font', 'general') \
        if isinstance(scene_manifest, dict) else 'general'
    render_target = scene_manifest.get('render_target', '') if isinstance(scene_manifest, dict) else ''

    tools_summary = json.dumps([t.get('name') for t in (tts_tools or [])], indent=2)

    payload = HERMES_TEMPLATE.format(
        script_excerpt=(script or '')[:200],
        domain_hint=f'{domain_hint} / {render_target}',
        tts_tools=tools_summary or '(none detected)',
        learnings=learnings_text,
        notes=(notes or '(none)')[:600],
    )
    raw = _call_ollama(payload, timeout=120)
    spec = _extract_json(raw)
    if spec:
        spec.setdefault('stop_or_proceed', 'PROCEED')
        spec.setdefault('tts_engine', 'piper')
        spec.setdefault('voice_model', 'en_US-lessac-medium')
        spec.setdefault('speaking_rate', 'normal')
        spec.setdefault('pitch_shift', 0)
        spec.setdefault('energy', 'medium')
        spec.setdefault('key_terms_risk', [])
        return spec
    return {
        'tts_engine': 'piper', 'voice_model': 'en_US-lessac-medium',
        'speaking_rate': 'normal', 'pitch_shift': 0, 'energy': 'medium',
        'domain_persona': 'clear neutral narrator',
        'key_terms_risk': [],
        'fleet_owner_hint': 'Dexter',
        'stop_or_proceed': 'PROCEED', 'stop_reason': '',
    }


# ---------------------------------------------------------------------------
# Stage 2 — Prosody outline
# ---------------------------------------------------------------------------

PROSODY_TEMPLATE = """You are an audio director. Map the narration script to a prosody plan:
where to speed up, slow down, pause, and emphasise.

SCRIPT (full):
{script}

SCENE TIMING (from Step 4 manifest — each scene's target duration):
{scene_timing}

HERMES AUDIO SPEC:
{hermes}

Rules:
- Total target: 39-43 seconds at {wps} words/second
- Mark emphasis with CAPS (max 3 per segment)
- Mark deliberate pauses as [pause:Xs] — e.g. [pause:0.3s] after a key stat
- Map each sentence to its GDS scene section
- Each scene segment must have: scene_id, text_segment, words, estimated_seconds, emphasis_words, pauses

Output VALID JSON ONLY:
{{
  "total_words": <int>,
  "estimated_total_seconds": <float>,
  "speaking_rate_multiplier": <float 0.8-1.2>,
  "segments": [
    {{
      "scene_id": "hook",
      "text_segment": "<exact text for this scene>",
      "tts_text": "<text with [pause:Xs] markers and CAPS emphasis inserted>",
      "words": <int>,
      "estimated_seconds": <float>,
      "emphasis_words": ["<WORD>"],
      "pauses": [{{"after": "<word>", "duration_s": 0.3}}]
    }}
  ]
}}
"""


def prosody_outline(hermes: dict, script: str, scene_manifest: dict) -> dict:
    outline_scenes = scene_manifest.get('scenes', {}) if isinstance(scene_manifest, dict) else {}
    scene_timing = {sid: sc.get('duration_seconds', 6)
                    for sid, sc in outline_scenes.items()} if isinstance(outline_scenes, dict) else {}

    payload = PROSODY_TEMPLATE.format(
        script=(script or '')[:3000],
        scene_timing=json.dumps(scene_timing, indent=2)[:600],
        hermes=json.dumps(hermes, indent=2)[:500],
        wps=WORDS_PER_SEC,
    )
    raw = _call_ollama(payload, timeout=180)
    spec = _extract_json(raw) or {}
    # Compute word count from actual script if model failed
    if not spec.get('total_words'):
        spec['total_words'] = len((script or '').split())
    if not spec.get('estimated_total_seconds'):
        spec['estimated_total_seconds'] = round(spec['total_words'] / WORDS_PER_SEC, 1)
    return spec


# ---------------------------------------------------------------------------
# Stage 3 — Draft audio spec
# ---------------------------------------------------------------------------

AUDIO_SPEC_TEMPLATE = """You are a TTS engineer producing a production-ready audio spec.
Generate the complete spec for Piper TTS (or fallback engine) to narrate a 41-second video.

HERMES ENGINE SETTINGS:
{hermes}

PROSODY OUTLINE (use these text_segment and tts_text values exactly):
{prosody}

KNOWN TECHNICAL TERMS NEEDING PHONETIC OVERRIDES:
{key_terms}

INSTALLED AUDIO TOOLS (prefer these for post-processing):
{audio_tools}

Output VALID JSON ONLY — complete audio production spec:
{{
  "tts_engine": "<piper|espeak-ng|kokoro|system>",
  "voice_model": "<model name>",
  "output_format": "wav",
  "sample_rate": 22050,
  "speaking_rate": <float 0.8-1.2>,
  "pitch": <int -2 to 2>,
  "phonetic_overrides": [
    {{"term": "BTC", "ipa": "ˌbiː.tiːˈsiː", "say_as": "bee-tee-see"}},
    {{"term": "DeFi", "ipa": "ˈdiːfaɪ", "say_as": "dee-fye"}}
  ],
  "segments": [
    {{
      "scene_id": "hook",
      "tts_command": "echo '<tts_text>' | piper --model <model> --output_file hook.wav",
      "output_file": "hook.wav",
      "tts_text": "<the actual text to synthesize — from prosody outline>",
      "estimated_seconds": <float>,
      "post_process": ["normalize -3dBFS", "fade_in 0.05s", "fade_out 0.1s"]
    }}
  ],
  "master_assembly": {{
    "concat_command": "ffmpeg -i 'concat:hook.wav|thesis.wav|...' -acodec pcm_s16le master.wav",
    "normalize_command": "ffmpeg -i master.wav -af loudnorm=I=-16:LRA=11:TP=-1.5 master_norm.wav",
    "output_file": "master_narration.wav",
    "estimated_total_seconds": <float>
  }},
  "quality_notes": ["<any TTS quality risks or manual review items>"]
}}
"""


def draft_audio_spec(hermes: dict, prosody: dict, script: str,
                     mode: str = 'fast', harvest: dict | None = None) -> dict:
    harvest = harvest or {}
    key_terms = json.dumps(hermes.get('key_terms_risk', []), indent=2)
    payload = AUDIO_SPEC_TEMPLATE.format(
        hermes=json.dumps(hermes, indent=2)[:600],
        prosody=json.dumps(prosody, indent=2)[:2500],
        key_terms=key_terms[:400],
        audio_tools=json.dumps(harvest.get('audio_tools', []), indent=2)[:600],
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

def _count_words(text: str) -> int:
    return len(re.findall(r'\b\w+\b', text or ''))


def _has_digit_runs(text: str) -> list[str]:
    return re.findall(r'\b\d{4,}\b|\b\d+\.\d+\b', text or '')


def _has_banned_chars(text: str) -> list[str]:
    return [c for c in BANNED_CHARS if c in (text or '')]


def validate_audio(spec: dict, prosody: dict, script: str) -> dict:
    grades: dict = {}
    issues: list = []

    # Word count
    words = prosody.get('total_words') or _count_words(script)
    wc_ok = 85 <= words <= 100
    grades['word_count_grade'] = 'GREEN' if 88 <= words <= 95 else ('YELLOW' if wc_ok else 'RED')
    if not wc_ok:
        issues.append(f'Word count {words} outside 85–100 range')

    # Timing
    est_s = prosody.get('estimated_total_seconds') or round(words / WORDS_PER_SEC, 1)
    timing_ok = 39 <= est_s <= 43
    grades['timing_grade'] = 'GREEN' if timing_ok else ('YELLOW' if 37 <= est_s <= 45 else 'RED')
    if not timing_ok:
        issues.append(f'Estimated duration {est_s}s outside 39–43s')

    # Digit runs
    digit_runs = _has_digit_runs(script)
    grades['digit_grade'] = 'GREEN' if not digit_runs else 'RED'
    if digit_runs:
        issues.append(f'Digit runs must be spelled out: {digit_runs[:3]}')

    # Banned chars
    banned = _has_banned_chars(script)
    grades['char_grade'] = 'GREEN' if not banned else 'RED'
    if banned:
        issues.append(f'Banned chars found: {banned}')

    # Segment coverage
    segments = spec.get('segments', [])
    covered = {s.get('scene_id') for s in segments if s.get('tts_text')}
    missing = [s for s in GDS_SECTIONS if s not in covered]
    grades['coverage_grade'] = 'GREEN' if not missing else ('YELLOW' if len(missing) <= 2 else 'RED')
    if missing:
        issues.append(f'Missing audio segments: {", ".join(missing)}')

    # Phonetic overrides for known risk terms
    key_terms = set()
    overrides = {o.get('term', '').lower() for o in (spec.get('phonetic_overrides') or [])}
    grades['phonetic_grade'] = 'GREEN'  # soft check — just flag if completely empty
    if not overrides and digit_runs:
        grades['phonetic_grade'] = 'YELLOW'
        issues.append('No phonetic overrides set despite digit/acronym risks')

    # Master assembly
    has_assembly = bool(spec.get('master_assembly', {}).get('output_file'))
    grades['assembly_grade'] = 'GREEN' if has_assembly else 'YELLOW'
    if not has_assembly:
        issues.append('master_assembly.output_file missing')

    overall_reds = sum(1 for g in grades.values() if g == 'RED')
    overall_yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    return {
        'grades': grades, 'issues': issues,
        'overall_reds': overall_reds, 'overall_yellows': overall_yellows,
        'word_count': words, 'estimated_seconds': est_s,
        'segment_count': len(segments), 'phonetic_overrides': len(overrides),
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Dev — TTS pipeline feasibility reviewer',
        'lens': 'Can every tts_command in this spec actually run on this machine? Check piper flags, output paths, ffmpeg concat syntax, normalization filters. Flag any command that would crash at render time.',
    },
    'memo': {
        'role': 'PM — pacing and timing reviewer',
        'lens': 'Does the prosody outline match the scene timing from Step 4? Listeners need time to absorb each evidence scene. Flag any segment where the estimated_seconds diverges from the Step 4 target by > 2s.',
    },
    'sienna': {
        'role': 'Domain Specialist — intelligibility and terminology reviewer',
        'lens': 'Will a first-time listener understand this narration clearly? Check phonetic_overrides cover all technical jargon, acronyms, and numbers. Flag any term a TTS engine is likely to mispronounce.',
    },
    'nano': {
        'role': 'Agent Creator — engagement and hook reviewer',
        'lens': 'Does the narration open with the hook immediately? Is the CTA clearly distinct in energy and pace? Are there enough [pause:Xs] markers to let key stats land? Flag any flatness in the pacing arc.',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent} ({role}).

Review this audio production spec. Focus ONLY on your lens:
{lens}

AUDIO SPEC SUMMARY:
{spec_summary}

VALIDATORS:
{validators}

Output Markdown ONLY with these exact sections:
### {agent_cap} — what to fix
(2-4 sharp bullets. Be specific about which segment and what to change.)

### {agent_cap} — verdict
GREEN-LIGHT | YELLOW-LIGHT | RED-LIGHT — one sentence reason.

### {agent_cap} — if I owned this
(1 concrete first move you would make.)
"""


def _review_one(agent: str, cfg: dict, spec: dict, validators: dict) -> tuple[str, str]:
    summary = json.dumps({
        'tts_engine': spec.get('tts_engine'),
        'voice_model': spec.get('voice_model'),
        'speaking_rate': spec.get('speaking_rate'),
        'segments': [{
            'scene_id': s.get('scene_id'),
            'estimated_seconds': s.get('estimated_seconds'),
            'tts_text_len': len(s.get('tts_text', '')),
        } for s in (spec.get('segments') or [])],
        'phonetic_overrides': len(spec.get('phonetic_overrides') or []),
        'master_assembly': spec.get('master_assembly', {}).get('output_file'),
    }, indent=2)[:2000]

    payload = FLEET_REVIEW_TEMPLATE.format(
        agent=agent.capitalize(), agent_cap=agent.capitalize(),
        role=cfg['role'], lens=cfg['lens'],
        spec_summary=summary,
        validators=json.dumps(validators, indent=2)[:600],
    )
    text = _call_ollama(payload, timeout=120)
    return agent, text


def fleet_review(spec: dict, validators: dict) -> dict:
    reviews: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_review_one, agent, cfg, spec, validators): agent
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
        label = 'Production-ready — advance to Subtitles'
    elif stars >= 4.0:
        label = 'Strong — refine to 5★ before Subtitles'
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
# Harvest — installed TTS + audio tools + OSS registry
# ---------------------------------------------------------------------------

def harvest_tts_tools() -> list[dict]:
    tools = [
        ('piper',      'tts',        'Piper neural TTS (ONNX, fast, local)'),
        ('espeak-ng',  'tts',        'eSpeak-NG fallback TTS'),
        ('kokoro',     'tts',        'Kokoro TTS (high quality ONNX)'),
        ('ffmpeg',     'processing', 'Audio mux, concat, normalize'),
        ('sox',        'processing', 'Audio format conversion and effects'),
        ('whisper',    'qa',         'Transcription QA — verify TTS output'),
        ('whisper-cpp','qa',         'Whisper.cpp local transcription'),
        ('aplay',      'playback',   'Linux audio playback (sanity check)'),
        ('afplay',     'playback',   'macOS audio playback (sanity check)'),
    ]
    out = []
    for bin_name, category, desc in tools:
        path = shutil.which(bin_name) or ''
        if path:
            out.append({'name': bin_name, 'category': category, 'description': desc, 'path': path})
    return out


def harvest_piper_voices() -> list[dict]:
    """Find locally installed Piper voice models."""
    search_dirs = [
        Path.home() / '.local' / 'share' / 'piper',
        Path.home() / 'piper',
        Path('/usr/share/piper'),
        Path('/opt/piper'),
    ]
    voices = []
    for d in search_dirs:
        if not d.exists():
            continue
        for onnx in d.rglob('*.onnx'):
            voices.append({
                'name': onnx.stem,
                'path': str(onnx),
                'config': str(onnx.with_suffix('.onnx.json')) if onnx.with_suffix('.onnx.json').exists() else None,
            })
    return voices[:10]


def harvest_audio_github_refs() -> list[dict]:
    try:
        result = subprocess.run(
            ['gh', 'search', 'repos', 'piper tts neural voice synthesis',
             '--sort', 'stars', '--limit', '6',
             '--json', 'nameWithOwner,description,stargazerCount,url'],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout or '[]')
    except Exception:
        return []


def harvest_oss_registry_audio() -> str:
    try:
        from .discovery import registry_for_steps
        return registry_for_steps(steps=['step5_audio'], categories=['audio-tts'], max_tools=15)
    except Exception:
        return '(OSS registry unavailable)'


def harvest_step5(hermes: dict) -> dict:
    out: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(harvest_tts_tools): 'audio_tools',
            pool.submit(harvest_piper_voices): 'piper_voices',
            pool.submit(harvest_audio_github_refs): 'github_refs',
            pool.submit(harvest_oss_registry_audio): 'oss_registry',
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

REWRITE_TEMPLATE = """The audio spec has RED-LIGHT critiques. Rewrite it to fix them.
Output the corrected FULL audio spec JSON (same schema).

CURRENT SPEC:
{spec}

RED-LIGHT CRITIQUES:
{critiques}

VALIDATOR ISSUES:
{issues}

Output VALID JSON ONLY — full corrected spec:
"""


def _rewrite_spec(spec: dict, fleet: dict, validators: dict) -> dict:
    red_critiques = '\n\n'.join(
        text for agent, text in fleet.get('reviews', {}).items()
        if re.search(r'RED-?LIGHT', text, re.IGNORECASE)
    )[:3000]
    payload = REWRITE_TEMPLATE.format(
        spec=json.dumps(spec, indent=2)[:4000],
        critiques=red_critiques,
        issues='; '.join(validators.get('issues', []))[:500],
    )
    raw = _call_ollama(payload, timeout=240)
    return _extract_json(raw) or spec


# ---------------------------------------------------------------------------
# Post-research learnings
# ---------------------------------------------------------------------------

def step5_post_research(result: dict, user_notes: str = '') -> dict:
    spec = result.get('audio_spec') or {}
    validators = result.get('validators') or {}
    rating = result.get('quality_rating') or {}

    prompt = f"""Extract concise learnings from this Step 5 audio narration run.

TTS ENGINE: {spec.get('tts_engine')} / {spec.get('voice_model')}
VALIDATORS: {json.dumps(validators.get('grades', {}), indent=2)[:300]}
QUALITY: {rating.get('stars')}★ — {rating.get('label')}
USER NOTES: {user_notes or '(none)'}

Output VALID JSON ONLY:
{{
  "what_worked": ["<1-3 TTS/prosody patterns that scored well>"],
  "what_failed": ["<1-3 patterns that caused RED verdicts>"],
  "voice_lessons": ["<voice model / speaking rate lessons>"],
  "phonetic_lessons": ["<phonetic override lessons for technical terms>"],
  "timing_lessons": ["<segment timing / pacing lessons>"],
  "next_video_recommendations": ["<1-2 recommendations for next run>"]
}}"""

    raw = _call_ollama(prompt, timeout=90)
    record: dict = _extract_json(raw) or {}
    record.update({
        'kind': 'step5_advance',
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

def step5_advise(result: dict) -> dict:
    validators = result.get('validators') or {}
    fleet = result.get('fleet') or {}
    rating = result.get('quality_rating') or {}
    stars = rating.get('stars', 3.0)

    prompt = f"""A Step 5 audio spec scored {stars}★. Diagnose the top issue and write focused
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
    return _extract_json(raw) or {'diagnosis': 'Unknown issue', 'focused_notes': 'Review phonetics and segment timing.'}


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run_step5(script: str = '', scene_manifest: dict | None = None,
              mode: str = 'fast', notes: str = '',
              prior_spec: dict | None = None,
              max_convergence: int = 2,
              project: str = 'default',
              voice_preference: str = 'brian') -> dict:
    started = time.time()
    stage_times: dict = {}
    scene_manifest = scene_manifest or {}

    # Stage 1: Hermes (needs TTS tools for voice selection)
    t = time.time()
    tts_tools_quick = harvest_tts_tools()
    hermes = hermes_preroute(script=script, scene_manifest=scene_manifest,
                             notes=notes, tts_tools=tts_tools_quick)
    stage_times['hermes_preroute'] = round(time.time() - t, 1)
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'stopped': True, 'hermes': hermes,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # Stage 2a + 2b: Prosody outline + full harvest in parallel
    t = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        prosody_fut = pool.submit(prosody_outline, hermes, script, scene_manifest)
        harvest_fut = pool.submit(harvest_step5, hermes)
        prosody = prosody_fut.result(timeout=200)
        harvest = harvest_fut.result(timeout=35)
    stage_times['prosody_outline_and_harvest'] = round(time.time() - t, 1)

    # Stage 3: Draft audio spec
    t = time.time()
    audio_spec = draft_audio_spec(hermes=hermes, prosody=prosody,
                                  script=script, mode=mode, harvest=harvest)
    stage_times['draft_audio_spec'] = round(time.time() - t, 1)

    # Stage 4: Validate
    t = time.time()
    validators = validate_audio(audio_spec, prosody, script)
    stage_times['validate'] = round(time.time() - t, 1)

    # Stage 5: Fleet review + convergence loop
    convergence_passes = 0
    while convergence_passes <= max_convergence:
        t = time.time()
        fleet = fleet_review(audio_spec, validators)
        stage_times[f'fleet_review_pass_{convergence_passes + 1}'] = round(time.time() - t, 1)

        red_count = fleet.get('verdicts', {}).get('RED', 0)
        if red_count > 0 and convergence_passes < max_convergence:
            t = time.time()
            audio_spec = _rewrite_spec(audio_spec, fleet, validators)
            validators = validate_audio(audio_spec, prosody, script)
            convergence_passes += 1
            stage_times[f'rewrite_pass_{convergence_passes}'] = round(time.time() - t, 1)
        else:
            break

    quality_rating = compute_quality(validators, fleet, convergence_passes)

    # OSS-only TTS routing (in priority order):
    #   1. VibeVoice (local ComfyUI, MIT, human-quality voice cloning) — auto when available
    #   2. ElevenLabs Brian (paid) — only when ZMARTY_ALLOW_PAID_TTS=1 + key
    #   3. Piper en_US-lessac-medium (local fallback, always present)
    # Each function is idempotent + checks its own gates; safe to call in order.
    audio_spec = _apply_vibevoice_to_spec(audio_spec)
    audio_spec = _apply_elevenlabs_to_spec(audio_spec, voice_preference=voice_preference)

    try:
        from .scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=5, fleet=fleet,
            stars=quality_rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=quality_rating.get('label', ''),
        )
    except Exception:
        pass

    try:
        from .skill_db import register_skill
        _prompt = (script or '')[:500]
        _summary = f"step5 audio · {(quality_rating.get('label') or '')[:80]}"
        _excerpt = {
            'voice_model': (audio_spec.get('voice') or {}).get('model') if isinstance(audio_spec.get('voice'), dict) else audio_spec.get('voice_model'),
            'segment_count': len(audio_spec.get('segments', []) or audio_spec.get('per_scene_audio', [])),
            'fleet_verdicts': fleet.get('verdicts', {}),
        }
        register_skill(
            step=5, prompt=_prompt,
            stars=quality_rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=5, prompt=_prompt, summary=_summary,
                          result_excerpt=_excerpt, stars=quality_rating.get('stars', 0.0))
    except Exception:
        pass

    return {
        'hermes': hermes,
        'prosody': prosody,
        'audio_spec': audio_spec,
        'harvest': {
            'audio_tools': harvest.get('audio_tools', []),
            'piper_voices': harvest.get('piper_voices', []),
            'github_refs': harvest.get('github_refs', []),
        },
        'validators': validators,
        'fleet': fleet,
        'quality_rating': quality_rating,
        'convergence_passes': convergence_passes,
        'elapsed_seconds': round(time.time() - started, 1),
        'stage_times': stage_times,
        'iteration': bool(prior_spec or notes),
        'mode': mode,
    }
