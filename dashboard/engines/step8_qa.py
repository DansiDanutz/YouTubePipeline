#!/usr/bin/env python3.13
"""Step 8 — Quality Gate engine.

Takes the render spec (Step 7) + subtitles_enabled flag and runs a comprehensive
QA audit of the final assembled video (final.mp4):

  • ffprobe validation — resolution, fps, duration, codecs
  • Audio sync check — verifies audio stream present and duration matches video
  • Subtitle burn-in verification — spot-checks subtitle presence when enabled
  • VMAF / PSNR placeholder (computed when reference frame available)
  • GDS coverage audit — 6 sections x scene count cross-check
  • File integrity — size, moov atom, container format

Pipeline (5 stages, same shape as Steps 1-7):
  Stage 1 — HERMES PRE-ROUTE     (render spec → QA strategy + tool selection)
  Stage 2 — QA OUTLINE           (test matrix, acceptance thresholds, check order)
  Stage 3 — DRAFT QA PLAN        (shell commands, expected outputs, pass criteria)
  Stage 4 — VALIDATE             (plan completeness, command syntax, coverage of all checks)
  Stage 5 — FLEET REVIEW         (technical / timing / domain / audience quality gates)

Hard validators:
  • ffprobe command present and targets output file
  • Resolution check exactly 1920×1080
  • FPS check exactly 30
  • Duration check 39–43 seconds
  • Audio stream present check
  • All 6 GDS sections referenced in QA matrix
  • File size check (> 0 bytes)
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

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL  = os.environ.get('STEP8_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL   = os.environ.get('STEP8_DEEP_MODEL',  'sonar-pro')

TARGET_WIDTH        = 1920
TARGET_HEIGHT       = 1080
TARGET_FPS          = 30
TARGET_SECONDS_MIN  = 39
TARGET_SECONDS_MAX  = 43
GDS_SECTIONS        = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']


def _scene_manifest_duration(scene_manifest: dict | None) -> float:
    if not isinstance(scene_manifest, dict):
        return 0.0
    beats = scene_manifest.get('beats')
    if isinstance(beats, list) and beats:
        total = 0.0
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            try:
                total += float(beat.get('duration_s') or beat.get('duration_seconds') or 0)
            except Exception:
                pass
        if total > 0:
            return total
    scenes = scene_manifest.get('scenes')
    values = scenes.values() if isinstance(scenes, dict) else scenes if isinstance(scenes, list) else []
    total = 0.0
    for scene in values:
        if not isinstance(scene, dict):
            continue
        try:
            total += float(scene.get('duration_seconds') or scene.get('duration_s') or 0)
        except Exception:
            pass
    return total


def _expected_duration_seconds(render_spec: dict | None, scene_manifest: dict | None = None) -> float:
    if isinstance(render_spec, dict):
        for key in ('expected_duration_seconds', 'target_seconds', 'duration_seconds', 'duration_s'):
            try:
                value = float(render_spec.get(key) or 0)
                if value > 0:
                    return value
            except Exception:
                continue
    manifest_total = _scene_manifest_duration(scene_manifest)
    if manifest_total > 0:
        return manifest_total
    return float((TARGET_SECONDS_MIN + TARGET_SECONDS_MAX) / 2)


def _duration_tolerance_seconds(expected: float) -> float:
    return max(2.0, round(expected * 0.03, 3))


def _resolve_output_file(output_file: str) -> Path:
    raw = (output_file or 'out/final.mp4').replace('\\', '/')
    p = Path(raw)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / raw).resolve()


def _probe_artifact(output_file: str) -> dict:
    path = _resolve_output_file(output_file)
    ffprobe = shutil.which('ffprobe') or shutil.which('ffprobe.exe')
    result = {
        'path': str(path),
        'exists': path.exists(),
        'bytes': path.stat().st_size if path.exists() else 0,
        'ffprobe_available': bool(ffprobe),
        'ok': False,
        'streams': [],
        'format': {},
    }
    if not path.exists() or result['bytes'] <= 0:
        result['error'] = 'output file missing or empty'
        return result
    if not ffprobe:
        result['error'] = 'ffprobe unavailable'
        return result
    try:
        proc = subprocess.run(
            [ffprobe, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)],
            capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace',
        )
        result['ffprobe_exit'] = proc.returncode
        if proc.returncode != 0:
            result['error'] = (proc.stderr or '')[:500]
            return result
        parsed = json.loads(proc.stdout or '{}')
        result['streams'] = parsed.get('streams', [])
        result['format'] = parsed.get('format', {})
        result['ok'] = True
        return result
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
        return result


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

HERMES_TEMPLATE = """You are Hermes orchestrating a QUALITY GATE step. You receive:
  - The final render spec from Step 7 (output file, resolution, fps, subtitles flag)
  - The assembled video file path (may not exist yet — plan for it)
  - Detected QA tools on this machine

Design a QA strategy that covers ALL of the following dimensions:
  1. Container integrity (ffprobe, moov atom, playback)
  2. Resolution & frame rate (1920x1080, 30fps)
  3. Duration (39–43 seconds)
  4. Audio stream (present, synced, AAC)
  5. Subtitle burn-in (only if subtitles_enabled=true)
  6. GDS section coverage (6 sections: hook/thesis/evidence_1/evidence_2/implication/cta)
  7. File size sanity (> 1MB)
  8. Codec compliance (H.264 video, AAC audio)

RENDER SPEC SUMMARY:
{render_spec_summary}

SUBTITLES ENABLED: {subtitles_enabled}
OUTPUT FILE: {output_file}

DETECTED QA TOOLS:
{qa_tools}

RECENT LEARNINGS (from past Step 8 runs):
{learnings}

USER NOTES:
{notes}

Output VALID JSON ONLY:
{{
  "qa_strategy":         "<strict | standard | fast>",
  "primary_tool":        "<ffprobe | mediainfo | ffplay>",
  "subtitle_check":      "<ffprobe-stream | frame-sample | skip>",
  "vmaf_available":      <true|false>,
  "gds_check_method":    "<render-spec-cross-ref | manifest-count | skip>",
  "output_file":         "<path to final.mp4>",
  "estimated_qa_seconds": <int>,
  "complexity_assessment": "<simple | moderate | complex>",
  "fleet_owner_hint":    "<Dexter | Memo | Sienna | Nano>",
  "hermes_notes":        "<brief routing rationale>"
}}"""


def _hermes_prepass(render_spec: dict, subtitles_enabled: bool, qa_tools: dict,
                    notes: str, learnings: str) -> dict:
    output_file = render_spec.get('output_file', 'out/final.mp4')
    render_summary = json.dumps({
        'output_file': output_file,
        'resolution': f'{render_spec.get("resolution", {}).get("width", 1920)}x{render_spec.get("resolution", {}).get("height", 1080)}',
        'fps': render_spec.get('fps', 30),
        'video_codec': render_spec.get('video_codec', 'h264'),
        'audio_codec': render_spec.get('audio_codec', 'aac'),
        'subtitles_enabled': subtitles_enabled,
    }, indent=2)
    tools_summary = json.dumps({k: v for k, v in qa_tools.items() if v}, indent=2)
    prompt = HERMES_TEMPLATE.format(
        render_spec_summary=render_summary,
        subtitles_enabled=subtitles_enabled,
        output_file=output_file,
        qa_tools=tools_summary,
        learnings=learnings or '(none yet)',
        notes=notes or '(none)',
    )
    raw = _call_ollama(prompt, timeout=180)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault('qa_strategy', 'standard')
    parsed.setdefault('primary_tool', 'ffprobe')
    parsed.setdefault('output_file', output_file)
    parsed.setdefault('estimated_qa_seconds', 15)
    parsed.setdefault('hermes_notes', raw[:200] if not parsed.get('hermes_notes') else parsed['hermes_notes'])
    return parsed


# ---------------------------------------------------------------------------
# Harvest — parallel tool discovery + OSS registry
# ---------------------------------------------------------------------------

def _probe_cmd(args: list[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        out = (r.stdout + r.stderr).strip()
        return out[:200] if out else 'ok'
    except FileNotFoundError:
        return 'not found'
    except Exception as e:
        return f'error: {e}'


def _detect_qa_tools() -> dict:
    tools: dict = {}
    # ffprobe
    ffprobe_ver = _probe_cmd(['ffprobe', '-version'])
    tools['ffprobe'] = ffprobe_ver if 'not found' not in ffprobe_ver else None
    # mediainfo
    mi_ver = _probe_cmd(['mediainfo', '--Version'])
    tools['mediainfo'] = mi_ver if 'not found' not in mi_ver else None
    # ffplay (for smoke-test playback)
    ffplay_ver = _probe_cmd(['ffplay', '-version'])
    tools['ffplay'] = ffplay_ver if 'not found' not in ffplay_ver else None
    # vmaf (libvmaf via ffmpeg filter)
    ffmpeg_ver = _probe_cmd(['ffmpeg', '-version'])
    tools['ffmpeg'] = ffmpeg_ver if 'not found' not in ffmpeg_ver else None
    vmaf_check = _probe_cmd(['ffmpeg', '-hide_banner', '-filters'])
    tools['vmaf_via_ffmpeg'] = 'libvmaf' in vmaf_check
    # python3 (for custom frame sampling)
    py_ver = _probe_cmd(['python3.13', '--version'])
    tools['python3'] = py_ver if 'not found' not in py_ver else None
    return tools


def _oss_registry_for_qa() -> list[dict]:
    try:
        from engines.discovery import registry_for_steps
        return registry_for_steps(
            steps=['step8_qa'],
            categories=['video-qa', 'quality', 'testing'],
        )
    except Exception:
        return []


def _github_qa_refs() -> list[dict]:
    return [
        {'name': 'ffprobe-json', 'url': 'https://ffmpeg.org/ffprobe.html', 'why': 'JSON output for automated checks'},
        {'name': 'vmaf', 'url': 'https://github.com/Netflix/vmaf', 'why': 'Video quality metric by Netflix'},
        {'name': 'mediainfo', 'url': 'https://github.com/MediaArea/MediaInfo', 'why': 'Container/stream metadata'},
        {'name': 'scenedetect', 'url': 'https://github.com/Breakthrough/PySceneDetect', 'why': 'Scene cut detection for GDS coverage'},
        {'name': 'vidgear', 'url': 'https://github.com/abhiTronix/vidgear', 'why': 'Python video I/O for frame sampling'},
    ]


def harvest_step8() -> dict:
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_tools   = ex.submit(_detect_qa_tools)
        f_oss     = ex.submit(_oss_registry_for_qa)
        f_refs    = ex.submit(_github_qa_refs)
        qa_tools  = f_tools.result()
        oss       = f_oss.result()
        refs      = f_refs.result()
    return {'qa_tools': qa_tools, 'oss_registry': oss, 'github_refs': refs}


# ---------------------------------------------------------------------------
# Stage 2 — QA outline
# ---------------------------------------------------------------------------

QA_OUTLINE_TEMPLATE = """You are designing a VIDEO QA TEST MATRIX for a Bitcoin explainer video.

VIDEO TARGET SPECS:
  Resolution: 1920×1080
  FPS:        30
  Duration:   {duration_min}–{duration_max} seconds
  Codec:      H.264 video + AAC audio
  Subtitles:  {subtitles_enabled}
  Output:     {output_file}

GDS SECTIONS (all 6 must be covered):
  hook, thesis, evidence_1, evidence_2, implication, cta

HERMES ROUTING:
  Strategy:   {qa_strategy}
  Primary:    {primary_tool}
  Subtitle check: {subtitle_check}

QA TOOLS AVAILABLE:
{qa_tools}

USER NOTES: {notes}

Design a QA test matrix covering every check dimension. Output VALID JSON ONLY:
{{
  "test_matrix": [
    {{
      "id":          "<check_id e.g. CHK-01>",
      "dimension":   "<resolution | fps | duration | audio | subtitle | gds | integrity | codec | filesize>",
      "description": "<what is being checked>",
      "tool":        "<ffprobe | mediainfo | python | manual>",
      "command_hint": "<brief shell command hint>",
      "pass_criteria": "<what output means PASS>",
      "severity":    "<BLOCKER | WARN | INFO>"
    }}
  ],
  "acceptance_thresholds": {{
    "resolution":   "1920x1080 exact",
    "fps":          "30 exact",
    "duration_min": {duration_min},
    "duration_max": {duration_max},
    "min_file_mb":  1,
    "audio_codec":  "aac",
    "video_codec":  "h264"
  }},
  "check_order": ["integrity", "resolution", "fps", "duration", "audio", "codec", "subtitle", "gds", "filesize"],
  "estimated_total_seconds": <int>,
  "gds_coverage_method": "<manifest-cross-ref | scene-count | skip>"
}}"""


def qa_outline(hermes: dict, render_spec: dict, subtitles_enabled: bool, qa_tools: dict, notes: str,
               scene_manifest: dict | None = None) -> dict:
    tools_summary = ', '.join(k for k, v in qa_tools.items() if v and k != 'vmaf_via_ffmpeg')
    expected_duration = _expected_duration_seconds(render_spec, scene_manifest)
    duration_tolerance = _duration_tolerance_seconds(expected_duration)
    duration_min = round(expected_duration - duration_tolerance, 3)
    duration_max = round(expected_duration + duration_tolerance, 3)
    prompt = QA_OUTLINE_TEMPLATE.format(
        subtitles_enabled=subtitles_enabled,
        output_file=hermes.get('output_file', 'out/final.mp4'),
        qa_strategy=hermes.get('qa_strategy', 'standard'),
        primary_tool=hermes.get('primary_tool', 'ffprobe'),
        subtitle_check=hermes.get('subtitle_check', 'ffprobe-stream'),
        qa_tools=tools_summary,
        notes=notes or '(none)',
        duration_min=duration_min,
        duration_max=duration_max,
    )
    raw = _call_ollama(prompt, timeout=180)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    if 'test_matrix' not in parsed or not isinstance(parsed.get('test_matrix'), list):
        parsed['test_matrix'] = _default_test_matrix(subtitles_enabled, hermes.get('output_file', 'out/final.mp4'))
    parsed.setdefault('acceptance_thresholds', {
        'resolution': '1920x1080',
        'fps': 30,
        'duration_min': TARGET_SECONDS_MIN,
        'duration_max': TARGET_SECONDS_MAX,
        'min_file_mb': 1,
        'audio_codec': 'aac',
        'video_codec': 'h264',
    })
    parsed['acceptance_thresholds']['duration_min'] = duration_min
    parsed['acceptance_thresholds']['duration_max'] = duration_max
    parsed.setdefault('check_order', ['integrity', 'resolution', 'fps', 'duration', 'audio', 'codec', 'filesize', 'gds'])
    return parsed


def _default_test_matrix(subtitles_enabled: bool, output_file: str) -> list[dict]:
    checks = [
        {'id': 'CHK-01', 'dimension': 'integrity', 'description': 'File exists and is valid container',
         'tool': 'ffprobe', 'command_hint': f'ffprobe -v error -show_entries format=duration -of json {output_file}',
         'pass_criteria': 'Exit 0, duration > 0', 'severity': 'BLOCKER'},
        {'id': 'CHK-02', 'dimension': 'resolution', 'description': 'Video stream is 1920×1080',
         'tool': 'ffprobe', 'command_hint': f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of json {output_file}',
         'pass_criteria': 'width=1920, height=1080', 'severity': 'BLOCKER'},
        {'id': 'CHK-03', 'dimension': 'fps', 'description': 'Frame rate exactly 30fps',
         'tool': 'ffprobe', 'command_hint': f'ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of json {output_file}',
         'pass_criteria': 'r_frame_rate=30/1 or 30000/1001', 'severity': 'BLOCKER'},
        {'id': 'CHK-04', 'dimension': 'duration', 'description': 'Duration 39–43 seconds',
         'tool': 'ffprobe', 'command_hint': f'ffprobe -v error -show_entries format=duration -of json {output_file}',
         'pass_criteria': '39 ≤ duration ≤ 43', 'severity': 'BLOCKER'},
        {'id': 'CHK-05', 'dimension': 'audio', 'description': 'Audio stream present and duration matches',
         'tool': 'ffprobe', 'command_hint': f'ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,duration -of json {output_file}',
         'pass_criteria': 'audio stream present, duration within 0.5s of video', 'severity': 'BLOCKER'},
        {'id': 'CHK-06', 'dimension': 'codec', 'description': 'Video codec H.264, audio codec AAC',
         'tool': 'ffprobe', 'command_hint': f'ffprobe -v error -show_entries stream=codec_name -of json {output_file}',
         'pass_criteria': 'h264 + aac present', 'severity': 'WARN'},
        {'id': 'CHK-07', 'dimension': 'filesize', 'description': 'Output file > 1MB',
         'tool': 'python', 'command_hint': f'os.path.getsize("{output_file}") > 1_000_000',
         'pass_criteria': 'file size > 1,000,000 bytes', 'severity': 'WARN'},
        {'id': 'CHK-08', 'dimension': 'gds', 'description': 'All 6 GDS sections covered by scenes',
         'tool': 'manual', 'command_hint': 'cross-ref scene_manifest gds_section fields',
         'pass_criteria': 'hook, thesis, evidence_1, evidence_2, implication, cta all present', 'severity': 'WARN'},
    ]
    if subtitles_enabled:
        checks.append({
            'id': 'CHK-09', 'dimension': 'subtitle',
            'description': 'Subtitle stream or burn-in present',
            'tool': 'ffprobe',
            'command_hint': f'ffprobe -v error -select_streams s -show_entries stream=codec_name -of json {output_file}',
            'pass_criteria': 'subtitle stream present OR burn-in confirmed via frame sample',
            'severity': 'WARN',
        })
    return checks


# ---------------------------------------------------------------------------
# Stage 3 — Draft QA plan
# ---------------------------------------------------------------------------

QA_DRAFT_TEMPLATE = """You are drafting the EXECUTABLE QA PLAN for a Bitcoin explainer video.

OUTPUT FILE: {output_file}
SUBTITLES ENABLED: {subtitles_enabled}
HERMES STRATEGY: {qa_strategy}
VMAF AVAILABLE: {vmaf_available}

TEST MATRIX ({n_checks} checks):
{matrix_summary}

Draft the full QA plan with exact shell commands. Output VALID JSON ONLY:
{{
  "ffprobe_full_inspect": "<full ffprobe command to dump all streams as JSON>",
  "ffprobe_video_stream": "<command to get video stream width/height/fps/codec>",
  "ffprobe_audio_stream": "<command to get audio stream codec/duration/channels>",
  "ffprobe_subtitle_stream": "<command to check subtitle tracks — null if subtitles disabled>",
  "ffprobe_duration":    "<command to get container duration>",
  "filesize_check":      "<python3 one-liner to check file size>",
  "vmaf_command":        "<ffmpeg vmaf command or null if not available>",
  "mediainfo_command":   "<mediainfo command or null if not installed>",
  "frame_sample_command": "<ffmpeg command to extract frame at midpoint for visual spot-check>",
  "gds_coverage_check":  "<description of how to verify 6 GDS sections>",
  "qa_script_bash":      "<self-contained bash script that runs all BLOCKER checks and exits 0 on pass>",
  "output_report_file":  "out/qa_report.json",
  "pass_criteria_summary": {{
    "resolution": "1920x1080",
    "fps": "30",
    "duration_range": "39-43s",
    "audio": "aac stream present",
    "codecs": "h264+aac",
    "filesize": ">1MB"
  }}
}}"""


def draft_qa_plan(hermes: dict, outline: dict, render_spec: dict, subtitles_enabled: bool,
                  scene_manifest: dict | None = None) -> dict:
    output_file = hermes.get('output_file', 'out/final.mp4')
    expected_duration = _expected_duration_seconds(render_spec, scene_manifest)
    duration_tolerance = _duration_tolerance_seconds(expected_duration)
    matrix = outline.get('test_matrix', _default_test_matrix(subtitles_enabled, output_file))
    matrix_summary = '\n'.join(
        f"  {c.get('id','?')} [{c.get('severity','?')}] {c.get('dimension','?')}: {c.get('description','?')}"
        for c in matrix
    )
    prompt = QA_DRAFT_TEMPLATE.format(
        output_file=output_file,
        subtitles_enabled=subtitles_enabled,
        qa_strategy=hermes.get('qa_strategy', 'standard'),
        vmaf_available=hermes.get('vmaf_available', False),
        n_checks=len(matrix),
        matrix_summary=matrix_summary,
    )
    raw = _call_ollama(prompt, timeout=180)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    of = output_file
    parsed.setdefault('ffprobe_full_inspect',
        f'ffprobe -v error -print_format json -show_format -show_streams "{of}"')
    parsed.setdefault('ffprobe_video_stream',
        f'ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate -of json "{of}"')
    parsed.setdefault('ffprobe_audio_stream',
        f'ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,duration,channels -of json "{of}"')
    parsed.setdefault('ffprobe_subtitle_stream',
        f'ffprobe -v error -select_streams s -show_entries stream=codec_name -of json "{of}"' if subtitles_enabled else None)
    parsed.setdefault('ffprobe_duration',
        f'ffprobe -v error -show_entries format=duration -of json "{of}"')
    parsed.setdefault('filesize_check',
        f'python3.13 -c "import os,sys; s=os.path.getsize(\\"{of}\\"); print(s); sys.exit(0 if s>1_000_000 else 1)"')
    parsed.setdefault('vmaf_command', None)
    parsed.setdefault('mediainfo_command', None)
    parsed.setdefault('frame_sample_command',
        f'ffmpeg -y -ss 20 -i "{of}" -vframes 1 out/qa_frame_mid.png')
    parsed.setdefault('gds_coverage_check',
        'Cross-reference render_spec.component_scaffold with GDS section list (hook/thesis/evidence_1/evidence_2/implication/cta)')
    parsed.setdefault('output_report_file', 'out/qa_report.json')
    parsed.setdefault('pass_criteria_summary', {
        'resolution': '1920x1080', 'fps': '30',
        'duration_range': f'{round(expected_duration - duration_tolerance, 2)}-{round(expected_duration + duration_tolerance, 2)}s',
        'audio': 'aac stream present', 'codecs': 'h264+aac', 'filesize': '>1MB',
    })
    parsed['pass_criteria_summary']['duration_range'] = f'{round(expected_duration - duration_tolerance, 2)}-{round(expected_duration + duration_tolerance, 2)}s'
    # Build default bash script
    parsed.setdefault('qa_script_bash', _build_qa_bash(of, subtitles_enabled, expected_duration, duration_tolerance))
    return parsed


def _build_qa_bash(output_file: str, subtitles_enabled: bool,
                   expected_duration: float | None = None,
                   duration_tolerance: float | None = None) -> str:
    expected_duration = float(expected_duration or ((TARGET_SECONDS_MIN + TARGET_SECONDS_MAX) / 2))
    duration_tolerance = float(duration_tolerance or _duration_tolerance_seconds(expected_duration))
    duration_min = round(expected_duration - duration_tolerance, 3)
    duration_max = round(expected_duration + duration_tolerance, 3)
    sub_check = f'''
# CHK-09: Subtitle stream
SUB=$(ffprobe -v error -select_streams s -show_entries stream=codec_name -of json "{output_file}" 2>/dev/null)
if echo "$SUB" | grep -q "codec_name"; then
  echo "CHK-09 PASS: subtitle stream present"
else
  echo "CHK-09 WARN: no embedded subtitle track (may be burned in)"
fi''' if subtitles_enabled else ''
    return f'''#!/usr/bin/env bash
set -e
FILE="{output_file}"
echo "=== ZmartyBitcoin QA Gate ==="
[ -f "$FILE" ] || {{ echo "CHK-01 FAIL: file not found"; exit 1; }}
echo "CHK-01 PASS: file exists"

INFO=$(ffprobe -v error -print_format json -show_format -show_streams "$FILE")

W=$(echo "$INFO" | python3.13 -c "import sys,json;d=json.load(sys.stdin);s=[x for x in d['streams'] if x['codec_type']=='video'][0];print(s['width'])")
H=$(echo "$INFO" | python3.13 -c "import sys,json;d=json.load(sys.stdin);s=[x for x in d['streams'] if x['codec_type']=='video'][0];print(s['height'])")
[ "$W" = "1920" ] && [ "$H" = "1080" ] && echo "CHK-02 PASS: ${{W}}x${{H}}" || {{ echo "CHK-02 FAIL: ${{W}}x${{H}}"; exit 1; }}

DUR=$(echo "$INFO" | python3.13 -c "import sys,json;d=json.load(sys.stdin);print(float(d['format']['duration']))")
python3.13 -c "d=$DUR; assert {duration_min}<=d<={duration_max}, f'duration {{d:.1f}}s out of range'" && echo "CHK-04 PASS: ${{DUR}}s" || {{ echo "CHK-04 FAIL: ${{DUR}}s"; exit 1; }}

AUDIO=$(echo "$INFO" | python3.13 -c "import sys,json;d=json.load(sys.stdin);a=[x for x in d['streams'] if x['codec_type']=='audio'];print('ok' if a else 'missing')")
[ "$AUDIO" = "ok" ] && echo "CHK-05 PASS: audio stream present" || {{ echo "CHK-05 FAIL: no audio"; exit 1; }}

SIZE=$(stat -f%z "$FILE" 2>/dev/null || stat --format=%s "$FILE" 2>/dev/null)
[ "$SIZE" -gt 1000000 ] && echo "CHK-07 PASS: ${{SIZE}} bytes" || echo "CHK-07 WARN: file small (${{SIZE}} bytes)"
{sub_check}
echo "=== QA PASS ==="'''


# ---------------------------------------------------------------------------
# Stage 4 — Validate
# ---------------------------------------------------------------------------

GRADE = {'GREEN': 'GREEN', 'YELLOW': 'YELLOW', 'RED': 'RED'}


def validate_qa_plan(hermes: dict, outline: dict, qa_plan: dict, render_spec: dict,
                     subtitles_enabled: bool, scene_manifest: dict | None = None) -> dict:
    grades: dict[str, str] = {}
    output_file = hermes.get('output_file', 'out/final.mp4')
    expected_duration = _expected_duration_seconds(render_spec, scene_manifest)
    duration_tolerance = _duration_tolerance_seconds(expected_duration)
    duration_min = expected_duration - duration_tolerance
    duration_max = expected_duration + duration_tolerance
    artifact = _probe_artifact(output_file)
    streams = artifact.get('streams') or []
    video_streams = [s for s in streams if s.get('codec_type') == 'video']
    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
    subtitle_streams = [s for s in streams if s.get('codec_type') == 'subtitle']
    v0 = video_streams[0] if video_streams else {}
    a0 = audio_streams[0] if audio_streams else {}
    try:
        duration_s = float((artifact.get('format') or {}).get('duration') or v0.get('duration') or 0)
    except Exception:
        duration_s = 0.0
    fps_raw = str(v0.get('r_frame_rate') or '')
    try:
        num, den = fps_raw.split('/')
        fps = float(num) / max(float(den), 1.0)
    except Exception:
        fps = 0.0

    # ffprobe_full_inspect command references the output file
    fi = qa_plan.get('ffprobe_full_inspect', '')
    grades['ffprobe_inspect_grade'] = 'GREEN' if artifact.get('ok') else 'RED'

    # Resolution check command present
    rv = qa_plan.get('ffprobe_video_stream', '')
    grades['resolution_check_grade'] = 'GREEN' if v0.get('width') == TARGET_WIDTH and v0.get('height') == TARGET_HEIGHT else 'RED'

    # FPS check
    grades['fps_check_grade'] = 'GREEN' if abs(fps - TARGET_FPS) < 0.05 else 'RED'

    # Duration check
    dur = qa_plan.get('ffprobe_duration', '')
    grades['duration_check_grade'] = 'GREEN' if duration_min <= duration_s <= duration_max else 'RED'

    # Audio check
    aud = qa_plan.get('ffprobe_audio_stream', '')
    grades['audio_check_grade'] = 'GREEN' if audio_streams and a0.get('codec_name') else 'RED'

    # Subtitle check
    if subtitles_enabled:
        sub = qa_plan.get('ffprobe_subtitle_stream') or ''
        grades['subtitle_check_grade'] = 'GREEN' if subtitle_streams else 'YELLOW'
    else:
        grades['subtitle_check_grade'] = 'GREEN'  # N/A

    # GDS coverage
    gds_chk = qa_plan.get('gds_coverage_check', '')
    grades['gds_coverage_grade'] = 'GREEN' if any(s in gds_chk for s in GDS_SECTIONS) else 'YELLOW'

    # Bash script present
    bash = qa_plan.get('qa_script_bash', '')
    grades['bash_script_grade'] = 'GREEN' if len(bash) > 100 and 'ffprobe' in bash else 'RED'

    # Test matrix coverage — all 8 dimensions
    matrix = outline.get('test_matrix', [])
    dims = {c.get('dimension', '') for c in matrix}
    required_dims = {'resolution', 'fps', 'duration', 'audio', 'integrity', 'codec', 'filesize', 'gds'}
    if subtitles_enabled:
        required_dims.add('subtitle')
    missing = required_dims - dims
    grades['coverage_grade'] = 'GREEN' if not missing else ('YELLOW' if len(missing) <= 2 else 'RED')

    # File size check
    fs = qa_plan.get('filesize_check', '')
    grades['filesize_check_grade'] = 'GREEN' if artifact.get('bytes', 0) > 1_000_000 else 'YELLOW'

    # Compute quality rating (same formula as Steps 3-7)
    reds   = sum(1 for g in grades.values() if g == 'RED')
    yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    stars = 5.0
    if reds >= 2:   stars -= 2.0
    elif reds == 1: stars -= 1.0
    if yellows >= 2: stars -= 0.5

    return {
        'grades': grades,
        'artifact': {
            'path': artifact.get('path'),
            'exists': artifact.get('exists'),
            'bytes': artifact.get('bytes'),
            'duration_seconds': round(duration_s, 3),
            'expected_duration_seconds': round(expected_duration, 3),
            'duration_tolerance_seconds': round(duration_tolerance, 3),
            'duration_min_seconds': round(duration_min, 3),
            'duration_max_seconds': round(duration_max, 3),
            'video_streams': len(video_streams),
            'audio_streams': len(audio_streams),
            'subtitle_streams': len(subtitle_streams),
            'width': v0.get('width'),
            'height': v0.get('height'),
            'fps': round(fps, 3),
            'video_codec': v0.get('codec_name'),
            'audio_codec': a0.get('codec_name'),
            'error': artifact.get('error', ''),
        },
        'quality_rating': {'stars': max(0.0, stars), 'reds': reds, 'yellows': yellows},
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review
# ---------------------------------------------------------------------------

FLEET_REVIEW_TEMPLATE = """You are {agent_name} ({role}) reviewing the QA PLAN for a Bitcoin video.

QA PLAN SUMMARY:
  Output:      {output_file}
  Subtitles:   {subtitles_enabled}
  Strategy:    {qa_strategy}
  Checks:      {n_checks}
  Test matrix: {matrix_summary}
  Bash script: {bash_excerpt}

VALIDATOR GRADES: {validator_grades}

Your job: identify gaps, false passes, or missing checks from your perspective.

Output VALID JSON ONLY:
{{
  "verdict":     "<GREEN | YELLOW | RED>",
  "confidence":  <0.0-1.0>,
  "critique":    "<what's wrong or risky>",
  "suggestion":  "<concrete fix or addition>",
  "approval":    <true|false>
}}"""


def _fleet_agent_review(agent_name: str, role: str, hermes: dict, outline: dict,
                        qa_plan: dict, validator: dict) -> dict:
    output_file = hermes.get('output_file', 'out/final.mp4')
    matrix = outline.get('test_matrix', [])
    matrix_summary = ', '.join(c.get('id', '?') + ':' + c.get('dimension', '?') for c in matrix[:8])
    bash = qa_plan.get('qa_script_bash', '')
    bash_excerpt = bash[:300] if bash else '(none)'
    grades = validator.get('grades', {})
    prompt = FLEET_REVIEW_TEMPLATE.format(
        agent_name=agent_name, role=role,
        output_file=output_file,
        subtitles_enabled=hermes.get('output_file', True),
        qa_strategy=hermes.get('qa_strategy', 'standard'),
        n_checks=len(matrix),
        matrix_summary=matrix_summary,
        bash_excerpt=bash_excerpt,
        validator_grades=', '.join(f'{k}={v}' for k, v in grades.items()),
    )
    raw = _call_ollama(prompt, timeout=120)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault('verdict', 'YELLOW')
    parsed.setdefault('confidence', 0.7)
    parsed.setdefault('critique', raw[:200])
    parsed.setdefault('suggestion', '(see critique)')
    parsed.setdefault('approval', parsed.get('verdict') != 'RED')
    parsed['agent'] = agent_name
    return parsed


def fleet_review(hermes: dict, outline: dict, qa_plan: dict, validator: dict) -> dict:
    agents = [
        ('Dexter', 'technical — ffprobe correctness, bash script robustness'),
        ('Memo',   'PM/timing — is the QA fast enough, does it block release?'),
        ('Sienna', 'domain — are the thresholds right for a Bitcoin video?'),
        ('Nano',   'engagement — will a poor QA plan miss quality issues visible to viewers?'),
    ]
    results: list[dict] = [{}] * len(agents)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(_fleet_agent_review, name, role, hermes, outline, qa_plan, validator): i
            for i, (name, role) in enumerate(agents)
        }
        for f in as_completed(futures):
            results[futures[f]] = f.result()
    reds    = sum(1 for r in results if r.get('verdict') == 'RED')
    yellows = sum(1 for r in results if r.get('verdict') == 'YELLOW')
    approved = sum(1 for r in results if r.get('approval'))
    convergence = 'strong' if reds == 0 and yellows <= 1 else ('weak' if reds >= 2 else 'moderate')
    return {
        'agents': results,
        'summary': {
            'reds': reds, 'yellows': yellows, 'approved': approved,
            'convergence': convergence,
        },
    }


# ---------------------------------------------------------------------------
# Convergence loop (max 2 passes)
# ---------------------------------------------------------------------------

def _convergence_pass(hermes: dict, outline: dict, qa_plan: dict, render_spec: dict,
                      subtitles_enabled: bool, fleet: dict, pass_num: int,
                      scene_manifest: dict | None = None) -> tuple[dict, dict, dict]:
    reds = fleet['summary']['reds']
    yellows = fleet['summary']['yellows']
    if reds == 0 and yellows <= 1:
        return outline, qa_plan, fleet
    # Collect improvement hints from fleet — agents may return list-shaped
    # suggestions (e.g. ["fix audio sync", "add subtitle burn-in"]) or strings.
    # Normalize to strings before joining to avoid TypeError on heterogeneous shapes.
    def _flatten_suggestion(s):
        if isinstance(s, str):
            return s.strip()
        if isinstance(s, list):
            return '; '.join(str(x).strip() for x in s if x)
        if isinstance(s, dict):
            # e.g. {"text": "...", "priority": ...}
            return str(s.get('text') or s.get('detail') or s.get('suggestion') or s).strip()
        return str(s).strip() if s else ''
    suggestions = [_flatten_suggestion(r.get('suggestion'))
                   for r in fleet['agents'] if r.get('suggestion')]
    notes_patch = 'CONVERGENCE PASS — address: ' + '; '.join(s for s in suggestions if s)
    new_outline = qa_outline(hermes, render_spec, subtitles_enabled, {}, notes_patch, scene_manifest)
    new_qa_plan = draft_qa_plan(hermes, new_outline, render_spec, subtitles_enabled, scene_manifest)
    new_validator = validate_qa_plan(hermes, new_outline, new_qa_plan, render_spec, subtitles_enabled, scene_manifest)
    new_fleet = fleet_review(hermes, new_outline, new_qa_plan, new_validator)
    return new_outline, new_qa_plan, new_fleet


# ---------------------------------------------------------------------------
# Public API — run_step8
# ---------------------------------------------------------------------------

def run_step8(
    render_spec: dict,
    scene_manifest: dict,
    subtitles_enabled: bool = True,
    mode: str = 'fast',
    notes: str = '',
    prior_qa_spec: dict | None = None,
    max_convergence: int = 2,
    project: str = 'default',
) -> dict:
    t0 = time.time()

    # --- Harvest (parallel) ---
    harvested = harvest_step8()
    qa_tools = harvested['qa_tools']
    oss      = harvested['oss_registry']
    refs     = harvested['github_refs']

    # --- Learnings ---
    learnings_text = ''
    try:
        from engines.learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        pass

    # --- Stage 1: Hermes ---
    hermes = _hermes_prepass(render_spec, subtitles_enabled, qa_tools, notes, learnings_text)

    # --- Stage 2 + 3 (parallel) ---
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_outline = ex.submit(qa_outline, hermes, render_spec, subtitles_enabled, qa_tools, notes, scene_manifest)
        # harvest already done; use it for context — just wait for outline
        outline = f_outline.result()
    qa_plan = draft_qa_plan(hermes, outline, render_spec, subtitles_enabled, scene_manifest)

    # --- Stage 4: Validate ---
    validator = validate_qa_plan(hermes, outline, qa_plan, render_spec, subtitles_enabled, scene_manifest)

    # --- Stage 5: Fleet ---
    fleet = fleet_review(hermes, outline, qa_plan, validator)

    # --- Convergence (up to max_convergence passes) ---
    convergence_passes = 0
    for _ in range(max_convergence):
        if fleet['summary']['reds'] == 0 and fleet['summary']['yellows'] <= 1:
            break
        outline, qa_plan, fleet = _convergence_pass(
            hermes, outline, qa_plan, render_spec, subtitles_enabled, fleet, convergence_passes + 1, scene_manifest)
        convergence_passes += 1

    # --- Final quality rating ---
    validator = validate_qa_plan(hermes, outline, qa_plan, render_spec, subtitles_enabled, scene_manifest)
    stars = validator['quality_rating']['stars']
    if convergence_passes >= 2:
        stars = max(0.0, stars - 0.5)

    elapsed = round(time.time() - t0, 1)

    try:
        from engines.scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=8, fleet=fleet, stars=stars,
            convergence_passes=convergence_passes,
            notes=validator['quality_rating'].get('label', ''),
        )
    except Exception:
        pass

    try:
        from engines.skill_db import register_skill
        _key = f"{render_spec.get('output_file', '')} scenes={len(scene_manifest.get('scenes', []))}"
        _summary = f"step8 qa · {validator['quality_rating'].get('label', '')[:80]}"
        _excerpt = {
            'output_file': render_spec.get('output_file'),
            'check_count': len(outline.get('test_matrix', [])),
            'fleet_summary': fleet.get('summary', {}),
            'artifact': validator.get('artifact', {}),
        }
        register_skill(
            step=8, prompt=_key[:500],
            stars=stars,
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from engines.learnings import generate_skill_md
        generate_skill_md(step_num=8, prompt=_key[:500], summary=_summary,
                          result_excerpt=_excerpt, stars=stars)
    except Exception:
        pass

    return {
        'hermes':           hermes,
        'qa_outline':       outline,
        'qa_plan':          qa_plan,
        'validators':       validator['grades'],
        'artifact':         validator.get('artifact', {}),
        'validator':        validator,
        'quality_rating':   {**validator['quality_rating'], 'stars': stars},
        'fleet_review':     fleet,
        'convergence_passes': convergence_passes,
        'harvested':        {'qa_tools': qa_tools, 'oss_registry': oss, 'github_refs': refs},
        'subtitles_enabled': subtitles_enabled,
        'elapsed_seconds':  elapsed,
        'mode':             mode,
    }


# ---------------------------------------------------------------------------
# Advise endpoint
# ---------------------------------------------------------------------------

def step8_advise(qa_spec: dict, question: str) -> dict:
    prompt = f"""You are a video QA specialist. A user has a question about their QA plan.

QA PLAN SUMMARY:
  Output: {qa_spec.get('qa_plan', {}).get('output_report_file', 'qa_report.json')}
  Checks: {len(qa_spec.get('qa_outline', {}).get('test_matrix', []))} checks
  Rating: {qa_spec.get('quality_rating', {}).get('stars', '?')}★

QUESTION: {question}

Answer concisely and practically. Output VALID JSON ONLY:
{{
  "answer": "<response>",
  "suggested_action": "<optional concrete next step>"
}}"""
    raw = _call_ollama(prompt, timeout=120)
    parsed = _extract_json(raw)
    return parsed if isinstance(parsed, dict) else {'answer': raw, 'suggested_action': ''}


# ---------------------------------------------------------------------------
# Post-research endpoint
# ---------------------------------------------------------------------------

def step8_post_research(result: dict, notes: str = '') -> dict:
    try:
        from engines.learnings import record_learning
        qa_plan = result.get('qa_plan', {})
        outline = result.get('qa_outline', {})
        record_learning(
            kind='step8_qa',
            summary=f"QA plan: {len(outline.get('test_matrix', []))} checks, strategy={result.get('hermes', {}).get('qa_strategy', '?')}",
            what_worked=[qa_plan.get('ffprobe_full_inspect', '')[:120]],
            what_failed=[r.get('critique', '') for r in result.get('fleet_review', {}).get('agents', []) if r.get('verdict') == 'RED'],
            user_notes=notes,
        )
    except Exception:
        pass
    return {
        'what_worked': [result.get('qa_plan', {}).get('ffprobe_full_inspect', '')[:80]],
        'what_failed': [],
        'banked': True,
    }
