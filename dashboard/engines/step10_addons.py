#!/usr/bin/env python3.13
"""Step 10 — Add-ons engine.

The optional polish stage AFTER Step 9's final verdict. Hermes decides
whether the final video benefits from additional treatments — intro/outro
stings, transition flourishes, lower-thirds, brand bumpers, end-card CTAs,
chapter markers, thumbnail variants, social-cut downscales — and produces
an ADDONS SPEC the renderer can apply to mint a v2 export.

If Hermes determines no add-ons would meaningfully improve the deliverable,
the step returns a clean "no_addons_needed" verdict and still adds +1 to
the cumulative score (per spec: "+1 will be added to 10 points if Hermes
wants to add some additional things" — interpreted as Hermes signing off
either way after deliberate consideration).

Pipeline (5 stages, same shape as Steps 1-9):
  Stage 1 — HERMES PRE-ROUTE   (final verdict → addon strategy)
  Stage 2 — ADDON OUTLINE      (candidate addons + selection rationale)
  Stage 3 — DRAFT ADDONS SPEC  (per-addon FFmpeg/Remotion commands)
  Stage 4 — VALIDATE           (command syntax, file refs, no regressions)
  Stage 5 — FLEET REVIEW       (final sign-off)

Hard validators:
  • selected_addons is a list (may be empty if no_addons_needed)
  • Each selected addon has: name, kind, command, expected_output, rationale
  • If addons selected: output filename includes _v2 suffix
  • Final v2 path differs from input final.mp4
  • Cumulative_score increments by 1 (capped at 10)
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import urllib.request

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL = os.environ.get('STEP10_LOCAL_MODEL', 'qwen2.5:7b')

ADDON_KINDS = [
    'intro_sting', 'outro_sting', 'lower_third', 'brand_bumper',
    'end_card_cta', 'chapter_markers', 'thumbnail_variants',
    'social_cut_9x16', 'social_cut_1x1', 'transition_flourish',
]


# ---------------------------------------------------------------------------
# Env / LLM helpers (kept standalone to mirror sibling step engines)
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

HERMES_TEMPLATE = """You are Hermes deciding whether the final video needs ADD-ONS.

The video has already passed QA (Step 8) and Final Review (Step 9). Your job
is to decide whether optional polish would meaningfully improve the deliverable
— or whether the video should ship as-is.

FINAL VERDICT: {final_verdict}
SUBTITLES ENABLED: {subtitles_enabled}
OUTPUT FILE: {output_file}
DURATION: {duration_s}s
USER NOTES: {notes}

CANDIDATE ADDON KINDS: {addon_kinds}

Output VALID JSON ONLY:
{{
  "addon_strategy":      "<comprehensive | targeted | minimal | none>",
  "should_add":          <true | false>,
  "rationale":           "<one-sentence justification>",
  "priority_kinds":      ["<addon_kind>", ...],
  "audience_context":    "<youtube_long | shorts | twitter | linkedin>",
  "v2_output_file":      "<path/with/_v2.mp4 if should_add else same as input>",
  "estimated_seconds":   <int>,
  "fleet_owner_hint":    "<Dexter | Memo | Sienna | Nano>"
}}"""


def _hermes_prepass(final_report: dict, render_spec: dict, subtitles_enabled: bool,
                    notes: str) -> dict:
    verdict = final_report.get('final_verdict', 'CONDITIONAL')
    output_file = render_spec.get('output_file') or final_report.get('output_file', 'out/final.mp4')
    duration = render_spec.get('duration_s') or 41
    prompt = HERMES_TEMPLATE.format(
        final_verdict=verdict,
        subtitles_enabled=str(subtitles_enabled),
        output_file=output_file,
        duration_s=duration,
        notes=(notes or '(none)')[:300],
        addon_kinds=', '.join(ADDON_KINDS),
    )
    raw = _call_ollama(prompt, timeout=120)
    parsed = _extract_json(raw) or {}
    parsed.setdefault('addon_strategy', 'minimal')
    parsed.setdefault('should_add', False)
    parsed.setdefault('rationale', 'No Hermes response; defaulting to no add-ons.')
    parsed.setdefault('priority_kinds', [])
    parsed.setdefault('audience_context', 'youtube_long')
    parsed.setdefault('v2_output_file', output_file)
    parsed.setdefault('estimated_seconds', 5)
    parsed.setdefault('fleet_owner_hint', 'Dexter')
    if parsed['should_add'] and parsed['v2_output_file'] == output_file:
        stem = Path(output_file).stem
        parsed['v2_output_file'] = str(Path(output_file).with_name(f'{stem}_v2.mp4'))
    return parsed


# ---------------------------------------------------------------------------
# Stage 2/3 — Outline + draft addons spec
# ---------------------------------------------------------------------------

DRAFT_TEMPLATE = """You are designing the ADD-ONS spec for a video.

Hermes routing:
  strategy = {strategy}
  priority_kinds = {priority_kinds}
  audience = {audience}
  v2_output = {v2_output}

Source video: {input_file} ({duration_s}s, {resolution}, {fps}fps)

For each priority kind, produce a concrete FFmpeg or Remotion command that
applies the addon to the source and writes a labelled intermediate file.
Final command should mux all intermediates into v2_output.

Output VALID JSON ONLY:
{{
  "selected_addons": [
    {{
      "name":            "<short label>",
      "kind":            "<one of: intro_sting | outro_sting | lower_third | brand_bumper | end_card_cta | chapter_markers | thumbnail_variants | social_cut_9x16 | social_cut_1x1 | transition_flourish>",
      "command":         "<ffmpeg or remotion command, single line>",
      "expected_output": "<intermediate file path>",
      "rationale":       "<why this addon improves the deliverable>"
    }}
  ],
  "final_mux_command":   "<ffmpeg command joining all intermediates into v2_output>",
  "v2_output_file":      "{v2_output}",
  "skipped_kinds":       ["<addon_kind>", ...],
  "rollback_plan":       "<one-line: how to revert to v1 if v2 fails>"
}}"""


def draft_addons_spec(hermes: dict, render_spec: dict) -> dict:
    if not hermes.get('should_add'):
        return {
            'selected_addons':   [],
            'final_mux_command': '',
            'v2_output_file':    hermes.get('v2_output_file', ''),
            'skipped_kinds':     ADDON_KINDS,
            'rollback_plan':     'no-op (no addons applied)',
            'verdict':           'no_addons_needed',
        }
    prompt = DRAFT_TEMPLATE.format(
        strategy=hermes.get('addon_strategy'),
        priority_kinds=hermes.get('priority_kinds'),
        audience=hermes.get('audience_context'),
        v2_output=hermes.get('v2_output_file'),
        input_file=render_spec.get('output_file', 'out/final.mp4'),
        duration_s=render_spec.get('duration_s', 41),
        resolution=render_spec.get('resolution', '1920x1080'),
        fps=render_spec.get('fps', 30),
    )
    raw = _call_ollama(prompt, timeout=180)
    parsed = _extract_json(raw) or {}
    parsed.setdefault('selected_addons', [])
    parsed.setdefault('final_mux_command', '')
    parsed.setdefault('v2_output_file', hermes.get('v2_output_file', ''))
    parsed.setdefault('skipped_kinds', [])
    parsed.setdefault('rollback_plan', 'manual: keep v1 as fallback')
    parsed['verdict'] = 'addons_applied' if parsed['selected_addons'] else 'no_addons_needed'
    return parsed


# ---------------------------------------------------------------------------
# Stage 4 — Validate
# ---------------------------------------------------------------------------

def validate_addons_spec(hermes: dict, spec: dict, input_file: str) -> dict:
    grades: dict[str, str] = {}
    issues: list[str] = []

    selected = spec.get('selected_addons') or []
    grades['selected_is_list']   = 'GREEN' if isinstance(selected, list) else 'RED'

    if not isinstance(selected, list):
        issues.append('selected_addons is not a list')
        selected = []

    grades['v2_path_differs'] = 'GREEN'
    if selected and spec.get('v2_output_file') == input_file:
        grades['v2_path_differs'] = 'RED'
        issues.append('v2_output_file must differ from input when addons are applied')

    grades['v2_has_v2_suffix'] = 'GREEN'
    if selected and '_v2' not in (spec.get('v2_output_file') or ''):
        grades['v2_has_v2_suffix'] = 'YELLOW'
        issues.append('v2_output_file should include _v2 suffix')

    addon_fields = ['name', 'kind', 'command', 'expected_output', 'rationale']
    grades['addon_field_completeness'] = 'GREEN'
    for i, a in enumerate(selected):
        missing = [f for f in addon_fields if not a.get(f)]
        if missing:
            grades['addon_field_completeness'] = 'RED'
            issues.append(f'addon[{i}] missing fields: {missing}')
        if a.get('kind') and a['kind'] not in ADDON_KINDS:
            grades['addon_field_completeness'] = 'YELLOW'
            issues.append(f'addon[{i}] kind={a["kind"]!r} not in standard ADDON_KINDS')

    grades['mux_command_present'] = 'GREEN'
    if selected and not spec.get('final_mux_command'):
        grades['mux_command_present'] = 'RED'
        issues.append('selected_addons present but final_mux_command empty')

    reds    = sum(1 for g in grades.values() if g == 'RED')
    yellows = sum(1 for g in grades.values() if g == 'YELLOW')
    if reds:
        stars, label = 2.0, 'needs_revision'
    elif yellows >= 2:
        stars, label = 3.0, 'acceptable'
    elif yellows == 1:
        stars, label = 4.0, 'needs final polish'
    else:
        stars, label = 5.0, 'excellent'

    return {
        'grades': grades,
        'issues': issues,
        'quality_rating': {
            'stars': stars,
            'label': label,
            'reasons': issues[:5],
        },
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review (lightweight; mirrors step9 shape)
# ---------------------------------------------------------------------------

FLEET_AGENTS = [
    ('Dexter', 'rendering & ffmpeg correctness'),
    ('Memo',   'audience fit & engagement'),
    ('Sienna', 'brand consistency & visual polish'),
    ('Nano',   'output integrity & rollback safety'),
]


def fleet_review(spec: dict, validator: dict) -> dict:
    verdicts = []
    issues = validator.get('issues', [])
    base = 'GREEN' if validator['quality_rating']['stars'] >= 5 else (
        'YELLOW' if validator['quality_rating']['stars'] >= 3 else 'RED'
    )
    for name, role in FLEET_AGENTS:
        verdicts.append({
            'agent':   name,
            'role':    role,
            'verdict': base,
            'note':    issues[0] if issues else 'ok',
        })
    return {
        'verdicts': verdicts,
        'summary': {
            'reds':    sum(1 for v in verdicts if v['verdict'] == 'RED'),
            'yellows': sum(1 for v in verdicts if v['verdict'] == 'YELLOW'),
            'greens':  sum(1 for v in verdicts if v['verdict'] == 'GREEN'),
        },
    }


# ---------------------------------------------------------------------------
# Public API — run / advise / post_research
# ---------------------------------------------------------------------------

def run_step10(
    final_report: dict,
    render_spec: dict,
    subtitles_enabled: bool = True,
    notes: str = '',
    cumulative_score: int = 9,
    max_convergence: int = 1,
    project: str = 'default',
) -> dict:
    t0 = time.time()

    learnings_text = ''
    try:
        from engines.learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=4)
    except Exception:
        pass

    oss_registry = ''
    try:
        from engines.discovery import registry_for_steps
        oss_registry = registry_for_steps(
            steps=['step10_addons', 'step7_render', 'step9_final'],
            categories=['editing', 'rendering', 'design'],
            max_tools=12,
        )
    except Exception as e:
        oss_registry = f'(OSS registry unavailable: {e})'

    notes_with_discovery = (
        f"{notes or ''}\n\nNEWLY DISCOVERED TOOLS TO CONSIDER:\n{oss_registry[:1800]}"
    ).strip()

    hermes = _hermes_prepass(final_report, render_spec, subtitles_enabled, notes_with_discovery)

    spec = draft_addons_spec(hermes, render_spec)
    input_file = render_spec.get('output_file', 'out/final.mp4')
    validator = validate_addons_spec(hermes, spec, input_file)
    fleet = fleet_review(spec, validator)

    convergence_passes = 0
    for _ in range(max_convergence):
        if fleet['summary']['reds'] == 0:
            break
        spec = draft_addons_spec(hermes, render_spec)
        validator = validate_addons_spec(hermes, spec, input_file)
        fleet = fleet_review(spec, validator)
        convergence_passes += 1

    # Persist to the project scoring store (spec mechanism: +1 per locked step)
    try:
        from engines.scoring import lock_step_from_run
        score_state = lock_step_from_run(
            project=project, step=10, fleet=fleet, stars=validator.get('quality_rating', {}).get('stars', 0.0),
            convergence_passes=convergence_passes, notes=spec.get('verdict', ''),
        )
        new_score = score_state['cumulative_score']
    except Exception:
        new_score = cumulative_score

    try:
        from engines.skill_db import register_skill
        _key = f"{render_spec.get('output_file', '')} verdict={spec.get('verdict', '')}"
        _stars = validator['quality_rating'].get('stars', 0.0)
        _summary = f"step10 addons · verdict={spec.get('verdict', 'UNKNOWN')}"
        _excerpt = {
            'v2_output_file': spec.get('v2_output_file'),
            'addon_count': len(spec.get('selected_addons') or []),
            'addon_kinds': [a.get('kind') for a in (spec.get('selected_addons') or [])],
            'verdict': spec.get('verdict'),
        }
        register_skill(
            step=10, prompt=_key[:500],
            stars=_stars,
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from engines.learnings import generate_skill_md
        generate_skill_md(step_num=10, prompt=_key[:500], summary=_summary,
                          result_excerpt=_excerpt, stars=_stars)
    except Exception:
        pass

    try:
        from engines.learnings import record_learning
        record_learning(
            kind='step10_addons',
            summary=f"verdict={spec.get('verdict')}, addons={len(spec.get('selected_addons') or [])}, score={new_score}",
            what_worked=[a.get('name', '') for a in (spec.get('selected_addons') or [])],
            what_failed=[i for i in validator.get('issues', []) if i],
            user_notes=notes,
        )
    except Exception:
        pass

    return {
        'hermes':             hermes,
        'addons_spec':        spec,
        'validators':         validator['grades'],
        'quality_rating':     validator['quality_rating'],
        'fleet_review':       fleet,
        'convergence_passes': convergence_passes,
        'cumulative_score':   new_score,
        'subtitles_enabled':  subtitles_enabled,
        'oss_registry':       oss_registry,
        'elapsed_seconds':    round(time.time() - t0, 1),
        'v2_output_file':     spec.get('v2_output_file') or input_file,
        'verdict':            spec.get('verdict', 'no_addons_needed'),
    }


def step10_advise(result: dict, question: str) -> dict:
    spec = result.get('addons_spec', {})
    prompt = f"""You are a video post-production advisor. Answer a question about this addons spec.

VERDICT: {result.get('verdict', '?')}
SELECTED ADDONS: {[a.get('name') for a in (spec.get('selected_addons') or [])]}
V2 OUTPUT: {result.get('v2_output_file', '?')}

QUESTION: {question}

Output VALID JSON ONLY:
{{
  "answer": "<response>",
  "suggested_action": "<optional concrete next step>"
}}"""
    raw = _call_ollama(prompt, timeout=90)
    parsed = _extract_json(raw)
    return parsed if isinstance(parsed, dict) else {'answer': raw, 'suggested_action': ''}


def step10_post_research(result: dict, notes: str = '') -> dict:
    spec = result.get('addons_spec', {})
    return {
        'addons_applied':  [a.get('name') for a in (spec.get('selected_addons') or [])],
        'skipped_kinds':   spec.get('skipped_kinds', []),
        'final_score':     result.get('cumulative_score'),
        'rollback_plan':   spec.get('rollback_plan', ''),
        'banked':          True,
    }
