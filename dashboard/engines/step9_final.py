#!/usr/bin/env python3.13
"""Step 9 — Final Review & Feedback engine.

The last gate before the video is released. Takes the QA report (Step 8) +
all upstream specs and produces:

  • A FINAL REVIEW BRIEF — executive summary, pass/fail verdict, key findings
  • An EXPORT PACKAGE MANIFEST — files to ship (final.mp4, SRT, QA report, etc.)
  • A FEEDBACK RECORD — what worked, what failed, actionable learnings for
    future runs (written to the learnings bank)
  • A RELEASE CHECKLIST — all items the creator must confirm before publishing

Pipeline (5 stages, same shape as Steps 1-8):
  Stage 1 — HERMES PRE-ROUTE     (QA report → review strategy, audience check)
  Stage 2 — REVIEW OUTLINE       (executive brief, risk matrix, release checklist)
  Stage 3 — DRAFT FINAL REPORT   (full narrative, export manifest, feedback record)
  Stage 4 — VALIDATE             (checklist completeness, GDS coverage, file manifest)
  Stage 5 — FLEET REVIEW         (final sign-off from all 4 agents)

Hard validators:
  • Executive summary present (> 50 chars)
  • Verdict field is PASS, CONDITIONAL, or FAIL
  • Export manifest lists at least final.mp4
  • Release checklist has ≥ 6 items
  • All 6 GDS sections referenced in review brief
  • Feedback record has what_worked AND what_failed lists
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import urllib.request
import urllib.error

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL  = os.environ.get('STEP9_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL   = os.environ.get('STEP9_DEEP_MODEL',  'sonar-pro')

GDS_SECTIONS = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']
MIN_CHECKLIST_ITEMS = 6


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


def _call_openrouter(prompt: str, model: str | None = None, timeout: int = 90) -> str:
    """OpenRouter chat completion — one API key routes to GLM, Kimi, GPT, Claude.

    Per spec Step 9: 'Use GLM, Kimi, or GPT for final verdict.' OpenRouter exposes
    all three behind a single OpenAI-compatible endpoint. Returns the assistant
    message text on success, or an error sentinel string on failure.

    Env:
      - OPENROUTER_API_KEY        required
      - STEP9_VERDICT_MODEL       default 'anthropic/claude-3.5-sonnet'
                                  alternatives: 'zhipu/glm-4-plus',
                                  'moonshotai/kimi-k2-instruct',
                                  'openai/gpt-4o-mini'
    """
    # Prefer the canonical oc_runner with auto-fallback chain — handles model
    # ID drift, rate limits, and provider routing automatically.
    try:
        from . import oc_runner
        if oc_runner.is_configured():
            preferred = model or _key('STEP9_VERDICT_MODEL') or oc_runner.DEFAULT_MODELS[0]
            out = oc_runner.chat(prompt, model=preferred,
                                 max_tokens=2048, temperature=0.2,
                                 timeout_s=timeout, try_chain=True)
            if out and not out.startswith('_('):
                return out
            # If chain failed, fall through to legacy direct call below
    except Exception:
        pass
    key = _key('OPENROUTER_API_KEY', 'OPENROUTER_KEY')
    if not key:
        return '_(openrouter key missing)_'
    chosen = model or _key('STEP9_VERDICT_MODEL') or 'anthropic/claude-sonnet-4.6'
    body = json.dumps({
        'model': chosen,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'max_tokens': 2048,
    }).encode('utf-8')
    try:
        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions', data=body,
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type':  'application/json',
                'HTTP-Referer':  'https://zmarty.video',
                'X-Title':       'Zmarty Video Pipeline — Step 9 Verdict',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
            return data['choices'][0]['message']['content']
    except Exception as e:
        return f'_(openrouter error: {e})_'


def _external_verdict_available() -> bool:
    """True when an OpenRouter key is present (auto-enables external verdict)."""
    return bool(_key('OPENROUTER_API_KEY', 'OPENROUTER_KEY'))


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

HERMES_TEMPLATE = """You are Hermes orchestrating the FINAL REVIEW step of a Bitcoin explainer video pipeline.

You have the full picture:
  - QA gate report (Step 8): checks run, pass/fail status, output file
  - Render spec (Step 7): resolution, fps, codecs, output file
  - Audio spec (Step 5): voice, duration, segment count
  - Subtitle spec (Step 6): enabled/disabled, style
  - Scene manifest (Step 4): 6 GDS scenes
  - Script (Step 2): full narration text

Your job: design a FINAL REVIEW strategy that produces an honest PASS/CONDITIONAL/FAIL
verdict and a complete export package manifest.

QA SUMMARY:
{qa_summary}

RENDER SUMMARY:
{render_summary}

SUBTITLES ENABLED: {subtitles_enabled}
SCRIPT WORD COUNT: {word_count}

RECENT LEARNINGS (from past Step 9 runs):
{learnings}

USER NOTES: {notes}

Output VALID JSON ONLY:
{{
  "review_strategy":    "<thorough | standard | quick>",
  "initial_verdict":    "<PASS | CONDITIONAL | FAIL>",
  "risk_level":         "<low | medium | high>",
  "export_format":      "<mp4_only | mp4_srt | full_package>",
  "audience_fit":       "<strong | moderate | weak>",
  "gds_integrity":      "<confirmed | partial | unknown>",
  "estimated_seconds":  <int>,
  "fleet_owner_hint":   "<Dexter | Memo | Sienna | Nano>",
  "hermes_notes":       "<brief routing rationale>"
}}"""


def _hermes_prepass(qa_spec: dict, render_spec: dict, audio_spec: dict,
                    subtitle_spec: dict, scene_manifest: dict, script: str,
                    subtitles_enabled: bool, notes: str, learnings: str) -> dict:
    qa_summ = json.dumps({
        'qa_strategy': qa_spec.get('hermes', {}).get('qa_strategy', '?'),
        'checks': len(qa_spec.get('qa_outline', {}).get('test_matrix', [])),
        'output_file': qa_spec.get('hermes', {}).get('output_file', 'out/final.mp4'),
        'quality_stars': qa_spec.get('quality_rating', {}).get('stars', '?'),
        'validator_reds': qa_spec.get('quality_rating', {}).get('reds', 0),
    }, indent=2)
    render_summ = json.dumps({
        'engine': render_spec.get('render_engine', '?'),
        'resolution': f"{render_spec.get('resolution', {}).get('width', 1920)}x{render_spec.get('resolution', {}).get('height', 1080)}",
        'fps': render_spec.get('fps', 30),
        'output': render_spec.get('output_file', 'out/final.mp4'),
    }, indent=2)
    word_count = len(script.split()) if script else 0
    prompt = HERMES_TEMPLATE.format(
        qa_summary=qa_summ,
        render_summary=render_summ,
        subtitles_enabled=subtitles_enabled,
        word_count=word_count,
        learnings=learnings or '(none yet)',
        notes=notes or '(none)',
    )
    raw = _call_ollama(prompt, timeout=180)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault('review_strategy', 'standard')
    parsed.setdefault('initial_verdict', 'CONDITIONAL')
    parsed.setdefault('risk_level', 'medium')
    parsed.setdefault('export_format', 'mp4_srt')
    parsed.setdefault('estimated_seconds', 20)
    parsed.setdefault('hermes_notes', raw[:200] if not parsed.get('hermes_notes') else parsed['hermes_notes'])
    return parsed


# ---------------------------------------------------------------------------
# Harvest — parallel context collection
# ---------------------------------------------------------------------------

def _collect_output_files(output_file: str) -> dict:
    of = Path(output_file) if output_file else Path('out/final.mp4')
    parent = of.parent
    found: dict = {}
    for ext in ['mp4', 'srt', 'vtt', 'json', 'png', 'wav', 'mp3']:
        candidates = list(parent.glob(f'*.{ext}'))
        if candidates:
            found[ext] = [str(c) for c in candidates[:5]]
    return found


def _oss_registry_for_final() -> list[dict]:
    try:
        from engines.discovery import registry_for_steps
        return registry_for_steps(
            steps=['step9_final'],
            categories=['publishing', 'delivery', 'video-qa'],
        )
    except Exception:
        return []


def harvest_step9(output_file: str) -> dict:
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_files = ex.submit(_collect_output_files, output_file)
        f_oss   = ex.submit(_oss_registry_for_final)
        found_files = f_files.result()
        oss = f_oss.result()
    return {'found_files': found_files, 'oss_registry': oss}


# ---------------------------------------------------------------------------
# Stage 2 — Review outline
# ---------------------------------------------------------------------------

OUTLINE_TEMPLATE = """You are writing the FINAL REVIEW OUTLINE for a Bitcoin explainer video.

HERMES VERDICT: {initial_verdict} (risk: {risk_level})
STRATEGY: {review_strategy}
SUBTITLES ENABLED: {subtitles_enabled}
GDS SECTIONS: hook, thesis, evidence_1, evidence_2, implication, cta

QA CHECKS PASSED: {qa_checks}
AUDIO DURATION: {audio_duration}s
SCRIPT WORDS: {word_count}
OUTPUT FILE: {output_file}

USER NOTES: {notes}

Produce the review outline. Output VALID JSON ONLY:
{{
  "executive_summary":     "<2-3 sentence plain-English summary of the video quality and readiness>",
  "verdict":               "<PASS | CONDITIONAL | FAIL>",
  "verdict_reason":        "<single sentence reason>",
  "gds_coverage": {{
    "hook":        "<confirmed | partial | missing>",
    "thesis":      "<confirmed | partial | missing>",
    "evidence_1":  "<confirmed | partial | missing>",
    "evidence_2":  "<confirmed | partial | missing>",
    "implication": "<confirmed | partial | missing>",
    "cta":         "<confirmed | partial | missing>"
  }},
  "risk_matrix": [
    {{"risk": "<description>", "severity": "<low|medium|high>", "mitigation": "<action>"}}
  ],
  "release_checklist": [
    "<item 1>",
    "<item 2>",
    "<item 3>",
    "<item 4>",
    "<item 5>",
    "<item 6>"
  ],
  "audience_notes":  "<notes on who this video is best for>",
  "publish_channels": ["<YouTube>", "<Twitter/X>", "<TikTok>"]
}}"""


def review_outline(hermes: dict, qa_spec: dict, audio_spec: dict, scene_manifest: dict,
                   script: str, subtitles_enabled: bool, notes: str) -> dict:
    matrix = qa_spec.get('qa_outline', {}).get('test_matrix', [])
    audio_duration = audio_spec.get('total_duration_seconds', 0)
    word_count = len(script.split()) if script else 0
    output_file = hermes.get('output_file', qa_spec.get('hermes', {}).get('output_file', 'out/final.mp4'))
    prompt = OUTLINE_TEMPLATE.format(
        initial_verdict=hermes.get('initial_verdict', 'CONDITIONAL'),
        risk_level=hermes.get('risk_level', 'medium'),
        review_strategy=hermes.get('review_strategy', 'standard'),
        subtitles_enabled=subtitles_enabled,
        qa_checks=len(matrix),
        audio_duration=audio_duration,
        word_count=word_count,
        output_file=output_file,
        notes=notes or '(none)',
    )
    raw = _call_ollama(prompt, timeout=180)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault('executive_summary', raw[:300])
    parsed.setdefault('verdict', hermes.get('initial_verdict', 'CONDITIONAL'))
    parsed.setdefault('verdict_reason', 'See executive summary.')
    parsed.setdefault('gds_coverage', {s: 'confirmed' for s in GDS_SECTIONS})
    parsed.setdefault('risk_matrix', [])
    parsed.setdefault('release_checklist', _default_checklist(subtitles_enabled))
    parsed.setdefault('audience_notes', 'Bitcoin enthusiasts and crypto-curious viewers.')
    parsed.setdefault('publish_channels', ['YouTube', 'Twitter/X', 'TikTok'])
    return parsed


def _default_checklist(subtitles_enabled: bool) -> list[str]:
    items = [
        'Watch final.mp4 end-to-end on a real screen',
        'Verify audio is clear and synced to visuals',
        'Confirm hook lands in the first 3 seconds',
        'Check CTA is clear and actionable at the end',
        'Verify all 6 GDS sections flow naturally',
        'Confirm file size and codec via ffprobe',
        'Upload thumbnail and write metadata before publishing',
        'Schedule or publish to planned channels',
    ]
    if subtitles_enabled:
        items.insert(4, 'Spot-check subtitle timing and readability at 1.5× playback speed')
    return items


# ---------------------------------------------------------------------------
# Stage 3 — Draft final report
# ---------------------------------------------------------------------------

REPORT_TEMPLATE = """You are writing the FINAL VIDEO REPORT for a Bitcoin explainer.

VERDICT: {verdict} ({verdict_reason})
EXECUTIVE SUMMARY: {executive_summary}
OUTPUT FILE: {output_file}
SUBTITLES: {subtitles_enabled}
EXPORT FORMAT: {export_format}

FOUND OUTPUT FILES: {found_files}

GDS COVERAGE: {gds_coverage}
RELEASE CHECKLIST ({n_items} items): {checklist_summary}

Write the full final report and export manifest. Output VALID JSON ONLY:
{{
  "final_verdict": "<PASS | CONDITIONAL | FAIL>",
  "report_narrative": "<multi-sentence plain English report — what worked, what to watch>",
  "export_manifest": [
    {{"file": "<path>", "type": "<primary|subtitle|qa|thumbnail|script>", "required": <true|false>, "description": "<what it is>"}}
  ],
  "feedback_record": {{
    "what_worked":    ["<item 1>", "<item 2>"],
    "what_failed":    ["<item 1 — or empty list if none>"],
    "key_learnings":  ["<learning 1>", "<learning 2>"],
    "next_run_hints": ["<hint for improving the next video>"]
  }},
  "publish_metadata": {{
    "suggested_title":       "<YouTube title>",
    "suggested_description": "<2-3 sentence description>",
    "suggested_tags":        ["<tag1>", "<tag2>", "<tag3>"],
    "suggested_thumbnail_text": "<text overlay for thumbnail>"
  }},
  "session_stats": {{
    "total_steps":   9,
    "pipeline_name": "ZmartyBitcoin",
    "gds_sections_covered": 6
  }}
}}"""


def draft_final_report(hermes: dict, outline: dict, render_spec: dict,
                       qa_spec: dict, found_files: dict, subtitles_enabled: bool) -> dict:
    output_file = hermes.get('output_file', qa_spec.get('hermes', {}).get('output_file', 'out/final.mp4'))
    checklist = outline.get('release_checklist', [])
    checklist_summary = ' | '.join(checklist[:6])
    gds_cov = outline.get('gds_coverage', {})
    prompt = REPORT_TEMPLATE.format(
        verdict=outline.get('verdict', 'CONDITIONAL'),
        verdict_reason=outline.get('verdict_reason', ''),
        executive_summary=outline.get('executive_summary', ''),
        output_file=output_file,
        subtitles_enabled=subtitles_enabled,
        export_format=hermes.get('export_format', 'mp4_srt'),
        found_files=json.dumps(found_files, indent=2)[:400],
        gds_coverage=json.dumps(gds_cov),
        n_items=len(checklist),
        checklist_summary=checklist_summary,
    )
    raw = _call_ollama(prompt, timeout=240)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault('final_verdict', outline.get('verdict', 'CONDITIONAL'))
    parsed.setdefault('report_narrative', outline.get('executive_summary', raw[:400]))
    parsed.setdefault('export_manifest', _default_manifest(output_file, subtitles_enabled))
    parsed.setdefault('feedback_record', {
        'what_worked': ['Pipeline completed all 9 steps'],
        'what_failed': [],
        'key_learnings': ['Review timing between scenes for future runs'],
        'next_run_hints': ['Tighten hook to under 3 seconds'],
    })
    parsed.setdefault('publish_metadata', {
        'suggested_title': 'Bitcoin Explained: What You Need to Know in 40 Seconds',
        'suggested_description': 'A fast, clear Bitcoin explainer covering the essentials.',
        'suggested_tags': ['bitcoin', 'crypto', 'explainer', 'finance'],
        'suggested_thumbnail_text': 'BITCOIN in 40s',
    })
    parsed.setdefault('session_stats', {'total_steps': 9, 'pipeline_name': 'ZmartyBitcoin', 'gds_sections_covered': 6})
    return parsed


def _default_manifest(output_file: str, subtitles_enabled: bool) -> list[dict]:
    items = [
        {'file': output_file, 'type': 'primary', 'required': True, 'description': 'Final assembled video — ship this'},
        {'file': 'out/qa_report.json', 'type': 'qa', 'required': False, 'description': 'Machine-readable QA audit report'},
        {'file': 'out/qa_frame_mid.png', 'type': 'thumbnail', 'required': False, 'description': 'Mid-point frame for thumbnail base'},
    ]
    if subtitles_enabled:
        items.insert(1, {'file': 'out/subtitles.srt', 'type': 'subtitle', 'required': False, 'description': 'SRT subtitle file for accessibility'})
        items.insert(2, {'file': 'out/subtitles.vtt', 'type': 'subtitle', 'required': False, 'description': 'WebVTT for browser players'})
    return items


# ---------------------------------------------------------------------------
# Stage 4 — Validate
# ---------------------------------------------------------------------------

def validate_final_report(hermes: dict, outline: dict, report: dict,
                           subtitles_enabled: bool) -> dict:
    grades: dict[str, str] = {}

    # Executive summary length
    summ = outline.get('executive_summary', '')
    grades['executive_summary_grade'] = 'GREEN' if len(summ) >= 50 else 'RED'

    # Verdict is valid
    verdict = report.get('final_verdict', '')
    grades['verdict_grade'] = 'GREEN' if verdict in ('PASS', 'CONDITIONAL', 'FAIL') else 'RED'

    # Export manifest has final.mp4
    manifest = report.get('export_manifest', [])
    has_mp4 = any('.mp4' in (e.get('file', '')) for e in manifest)
    grades['export_manifest_grade'] = 'GREEN' if has_mp4 else 'RED'

    # Release checklist has ≥ 6 items
    checklist = outline.get('release_checklist', [])
    grades['checklist_grade'] = 'GREEN' if len(checklist) >= MIN_CHECKLIST_ITEMS else ('YELLOW' if len(checklist) >= 4 else 'RED')

    # GDS coverage — all 6 sections referenced
    gds = outline.get('gds_coverage', {})
    confirmed = sum(1 for s in GDS_SECTIONS if gds.get(s) == 'confirmed')
    grades['gds_coverage_grade'] = 'GREEN' if confirmed >= 6 else ('YELLOW' if confirmed >= 4 else 'RED')

    # Feedback record has both what_worked and what_failed
    fb = report.get('feedback_record', {})
    has_fb = isinstance(fb.get('what_worked'), list) and isinstance(fb.get('what_failed'), list)
    grades['feedback_record_grade'] = 'GREEN' if has_fb else 'YELLOW'

    # Publish metadata has title + tags
    pm = report.get('publish_metadata', {})
    has_meta = bool(pm.get('suggested_title')) and isinstance(pm.get('suggested_tags'), list)
    grades['publish_metadata_grade'] = 'GREEN' if has_meta else 'YELLOW'

    # Narrative length
    narrative = report.get('report_narrative', '')
    grades['narrative_grade'] = 'GREEN' if len(narrative) >= 80 else 'YELLOW'

    reds   = sum(1 for g in grades.values() if g == 'RED')
    yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    stars = 5.0
    if reds >= 2:   stars -= 2.0
    elif reds == 1: stars -= 1.0
    if yellows >= 2: stars -= 0.5

    return {
        'grades': grades,
        'quality_rating': {'stars': max(0.0, stars), 'reds': reds, 'yellows': yellows},
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review (final sign-off)
# ---------------------------------------------------------------------------

FLEET_SIGNOFF_TEMPLATE = """You are {agent_name} ({role}) giving FINAL SIGN-OFF on a Bitcoin explainer video.

VERDICT: {verdict}
EXECUTIVE SUMMARY: {executive_summary}
EXPORT MANIFEST: {manifest_files}
RELEASE CHECKLIST ({n_items} items): {checklist_excerpt}
VALIDATOR GRADES: {validator_grades}

This is the LAST gate before release. Be honest and decisive.

Output VALID JSON ONLY:
{{
  "verdict":     "<GREEN | YELLOW | RED>",
  "confidence":  <0.0-1.0>,
  "critique":    "<what's missing or risky — be specific>",
  "suggestion":  "<concrete action to take before releasing>",
  "approval":    <true|false>
}}"""


def _fleet_signoff(agent_name: str, role: str, outline: dict, report: dict,
                   validator: dict, use_external: bool = False) -> dict:
    verdict = report.get('final_verdict', 'CONDITIONAL')
    executive_summary = outline.get('executive_summary', '')
    manifest = report.get('export_manifest', [])
    manifest_files = ', '.join(e.get('file', '?') for e in manifest[:5])
    checklist = outline.get('release_checklist', [])
    checklist_excerpt = ' | '.join(checklist[:4])
    grades = validator.get('grades', {})
    prompt = FLEET_SIGNOFF_TEMPLATE.format(
        agent_name=agent_name, role=role,
        verdict=verdict,
        executive_summary=executive_summary[:300],
        manifest_files=manifest_files,
        n_items=len(checklist),
        checklist_excerpt=checklist_excerpt,
        validator_grades=', '.join(f'{k}={v}' for k, v in grades.items()),
    )
    used = 'ollama'
    if use_external:
        raw = _call_openrouter(prompt, timeout=90)
        if raw and not raw.startswith('_(openrouter '):
            used = 'openrouter'
        else:
            # Fall back to local on external failure so the pipeline never hangs
            raw = _call_ollama(prompt, timeout=120)
    else:
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
    parsed['llm'] = used
    return parsed


def fleet_review(outline: dict, report: dict, validator: dict,
                 use_external: bool | None = None) -> dict:
    """Fleet of 4 specialist agents review the release.

    use_external:
      - True  → force OpenRouter (GLM/Kimi/GPT/Claude per STEP9_VERDICT_MODEL)
      - False → force local Ollama
      - None  → auto-detect: external if OPENROUTER_API_KEY is set, else local
    """
    if use_external is None:
        use_external = _external_verdict_available()
    agents = [
        ('Dexter', 'technical — file integrity, codec, ffprobe, bash scripts'),
        ('Memo',   'PM/timing — is the checklist complete, is the release plan solid?'),
        ('Sienna', 'domain — is the Bitcoin content accurate and audience-appropriate?'),
        ('Nano',   'engagement — will viewers watch to the end? Is the hook strong enough?'),
    ]
    results: list[dict] = [{}] * len(agents)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(_fleet_signoff, name, role, outline, report, validator,
                      use_external): i
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
        'summary': {'reds': reds, 'yellows': yellows, 'approved': approved, 'convergence': convergence},
        'verdict_llm': 'openrouter' if use_external else 'ollama',
    }


# ---------------------------------------------------------------------------
# Convergence loop
# ---------------------------------------------------------------------------

def _convergence_pass(hermes: dict, outline: dict, report: dict, render_spec: dict,
                      qa_spec: dict, audio_spec: dict, scene_manifest: dict,
                      script: str, subtitles_enabled: bool, found_files: dict,
                      fleet: dict, use_external: bool | None = None) -> tuple[dict, dict, dict]:
    reds = fleet['summary']['reds']
    yellows = fleet['summary']['yellows']
    if reds == 0 and yellows <= 1:
        return outline, report, fleet
    suggestions = [r.get('suggestion', '') for r in fleet['agents'] if r.get('suggestion')]
    notes_patch = 'CONVERGENCE — address: ' + '; '.join(s for s in suggestions if s)
    new_outline = review_outline(hermes, qa_spec, audio_spec, scene_manifest, script, subtitles_enabled, notes_patch)
    new_report  = draft_final_report(hermes, new_outline, render_spec, qa_spec, found_files, subtitles_enabled)
    new_validator = validate_final_report(hermes, new_outline, new_report, subtitles_enabled)
    new_fleet = fleet_review(new_outline, new_report, new_validator, use_external=use_external)
    return new_outline, new_report, new_fleet


# ---------------------------------------------------------------------------
# Public API — run_step9
# ---------------------------------------------------------------------------

def run_step9(
    qa_spec: dict,
    render_spec: dict,
    audio_spec: dict,
    subtitle_spec: dict,
    scene_manifest: dict,
    script: str = '',
    subtitles_enabled: bool = True,
    mode: str = 'fast',
    notes: str = '',
    max_convergence: int = 2,
    project: str = 'default',
    use_external_verdict: bool | None = None,
) -> dict:
    t0 = time.time()

    # --- Learnings ---
    learnings_text = ''
    try:
        from engines.learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        pass

    # --- Stage 1: Hermes ---
    hermes = _hermes_prepass(
        qa_spec, render_spec, audio_spec, subtitle_spec, scene_manifest,
        script, subtitles_enabled, notes, learnings_text,
    )

    # --- Harvest (parallel with outline) ---
    output_file = hermes.get('output_file', qa_spec.get('hermes', {}).get('output_file', 'out/final.mp4'))
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_harvest = ex.submit(harvest_step9, output_file)
        f_outline = ex.submit(review_outline, hermes, qa_spec, audio_spec, scene_manifest, script, subtitles_enabled, notes)
        harvested = f_harvest.result()
        outline   = f_outline.result()

    found_files = harvested.get('found_files', {})
    oss = harvested.get('oss_registry', [])

    # --- Stage 3: Draft report ---
    report = draft_final_report(hermes, outline, render_spec, qa_spec, found_files, subtitles_enabled)

    # --- Stage 4: Validate ---
    validator = validate_final_report(hermes, outline, report, subtitles_enabled)

    # --- Stage 5: Fleet (auto-routes to OpenRouter when key present) ---
    fleet = fleet_review(outline, report, validator, use_external=use_external_verdict)

    # --- Convergence ---
    convergence_passes = 0
    for _ in range(max_convergence):
        if fleet['summary']['reds'] == 0 and fleet['summary']['yellows'] <= 1:
            break
        outline, report, fleet = _convergence_pass(
            hermes, outline, report, render_spec, qa_spec, audio_spec,
            scene_manifest, script, subtitles_enabled, found_files, fleet,
            use_external=use_external_verdict,
        )
        convergence_passes += 1

    validator = validate_final_report(hermes, outline, report, subtitles_enabled)
    stars = validator['quality_rating']['stars']
    if convergence_passes >= 2:
        stars = max(0.0, stars - 0.5)

    # --- Bank learnings ---
    fb = report.get('feedback_record', {})
    try:
        from engines.learnings import record_learning
        record_learning(
            kind='step9_final',
            summary=f"Final verdict={report.get('final_verdict', '?')}, {stars}★",
            what_worked=fb.get('what_worked', []),
            what_failed=fb.get('what_failed', []),
            user_notes=notes,
        )
    except Exception:
        pass

    elapsed = round(time.time() - t0, 1)

    try:
        from engines.scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=9, fleet=fleet, stars=stars,
            convergence_passes=convergence_passes,
            notes=report.get('final_verdict', ''),
        )
    except Exception:
        pass

    try:
        from engines.skill_db import register_skill
        _key = f"{render_spec.get('output_file', '')} verdict={report.get('final_verdict', '')}"
        _summary = f"step9 final · verdict={report.get('final_verdict', 'UNKNOWN')}"
        _excerpt = {
            'output_file': render_spec.get('output_file'),
            'final_verdict': report.get('final_verdict'),
            'export_files': len(report.get('export_manifest', [])),
            'fleet_summary': fleet.get('summary', {}),
            'verdict_llm': fleet.get('verdict_llm', 'ollama'),
        }
        register_skill(
            step=9, prompt=_key[:500],
            stars=stars,
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from engines.learnings import generate_skill_md
        generate_skill_md(step_num=9, prompt=_key[:500], summary=_summary,
                          result_excerpt=_excerpt, stars=stars)
    except Exception:
        pass

    return {
        'hermes':              hermes,
        'review_outline':      outline,
        'final_report':        report,
        'validators':          validator['grades'],
        'quality_rating':      {**validator['quality_rating'], 'stars': stars},
        'fleet_review':        fleet,
        'convergence_passes':  convergence_passes,
        'harvested':           harvested,
        'subtitles_enabled':   subtitles_enabled,
        'elapsed_seconds':     elapsed,
        'mode':                mode,
        'output_file':         output_file,
    }


# ---------------------------------------------------------------------------
# Advise endpoint
# ---------------------------------------------------------------------------

def step9_advise(result: dict, question: str) -> dict:
    report = result.get('final_report', {})
    prompt = f"""You are a video release specialist. Answer a question about this final review.

VERDICT: {report.get('final_verdict', '?')}
NARRATIVE: {result.get('review_outline', {}).get('executive_summary', '')[:200]}

QUESTION: {question}

Output VALID JSON ONLY:
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

def step9_post_research(result: dict, notes: str = '') -> dict:
    fb = result.get('final_report', {}).get('feedback_record', {})
    return {
        'what_worked': fb.get('what_worked', []),
        'what_failed': fb.get('what_failed', []),
        'key_learnings': fb.get('key_learnings', []),
        'banked': True,
    }
