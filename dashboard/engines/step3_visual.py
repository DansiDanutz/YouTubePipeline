#!/usr/bin/env python3.13
"""Step 3 — Visual Direction & Design System engine.

Domain-agnostic. Takes Step 1's locked research brief + Step 2's locked
narration script and produces the VISUAL LANGUAGE the video will speak in:
color palette with hex codes, typography pairings, motion principles,
texture/atmosphere notes, and per-section visual treatments mapped to the
6 GDS scenes (Hook / Thesis / Evidence×2 / Implication / CTA).

Pipeline (5 stages, same shape as Steps 1-2):
  Stage 1 — HERMES PRE-ROUTE   (domain → visual constraints)
  Stage 2 — VISUAL OUTLINE     (mood + concept + reference signals)
  Stage 3 — DRAFT DESIGN SYSTEM (colors, typography, motion, scenes)
  Stage 4 — POLISH + VALIDATE  (hex codes, palette size, scene coverage)
  Stage 5 — FLEET REVIEW       (render feasibility / consistency / domain
                                authenticity / engagement)

Hard validators on the design system:
  • 3-6 colors with valid hex codes (#XXXXXX or #XXX)
  • Display + body typography both identified
  • Motion principles list compositor-friendly properties only
    (transform, opacity, clip-path, filter — NOT width/height/top/left)
  • Per-scene treatments present for all 6 GDS sections
  • No banned cliches in the description (centered hero w/ gradient blob,
    uniform card grid, "minimal & clean" without specifics)
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
LOCAL_MODEL = os.environ.get('STEP3_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL = os.environ.get('STEP3_DEEP_MODEL', 'sonar-pro')

GDS_SECTIONS = ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']
COMPOSITOR_FRIENDLY = {'transform', 'opacity', 'clip-path', 'filter', 'translate', 'scale', 'rotate'}
LAYOUT_BOUND = {'width', 'height', 'top', 'left', 'margin', 'padding', 'border', 'font-size'}
BANNED_CLICHES = [
    'centered hero with gradient blob',
    'uniform card grid',
    'minimal and clean',
    'sleek and modern',
    'cutting edge',
    'next generation',
]


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
    req = urllib.request.Request(f'{OLLAMA_HOST}/api/chat', data=body,
                                 headers=_ollama_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return data.get('message', {}).get('content', '').strip()
    except Exception as e:
        return f'_(Ollama error: {e})_'


def _call_perplexity(prompt: str, model: str = DEEP_MODEL, timeout: int = 240) -> str:
    key = _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY')
    if not key:
        return ''
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}]}).encode('utf-8')
    try:
        req = urllib.request.Request(
            'https://api.perplexity.ai/chat/completions', data=body,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        choices = data.get('choices', [])
        return choices[0].get('message', {}).get('content', '').strip() if choices else ''
    except Exception as e:
        return f'_(Perplexity error: {e})_'


def _strip_to_text(raw: str) -> str:
    s = (raw or '').strip()
    s = re.sub(r'^```(?:[a-zA-Z]+)?\s*|\s*```\s*$', '', s, flags=re.MULTILINE)
    return s.strip()


def _extract_json(raw: str) -> dict | None:
    cleaned = _strip_to_text(raw)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stage 1 — Hermes pre-route (visual variant)
# ---------------------------------------------------------------------------

HERMES_TEMPLATE = """You are Hermes orchestrating a VISUAL DIRECTION step. The user has a research
brief (Step 1) and a TTS-ready script (Step 2). You now design the visual language.

The TOPIC AND DOMAIN come ENTIRELY from the user's prior steps. Do NOT inject any
visual style assumption that the user did not implicitly request. Examples this engine
must serve equally well: crypto explainers (dark fintech), cooking videos (warm domestic),
educational content (clean minimal academic), music videos (high-contrast saturated),
B2B SaaS demos (corporate clean), documentaries (muted editorial).

USER'S STEP 1 RESEARCH BRIEF (locked):
{step1_brief}

USER'S STEP 2 NARRATION SCRIPT (locked, drives pacing):
{step2_script}

USER NOTES on this iteration (may be empty):
{notes}

RECENT LEARNINGS (from past Step 3 runs — what design patterns worked, what failed).
USE THESE to bias your visual archetype and exclude styles that previously scored low.
{learnings}

Output VALID JSON ONLY — describe the visual constraints WITHOUT writing the design yet:
{{
  "topic_class": "<auto-detected from steps 1+2>",
  "domain": "<the actual subject area as a sentence>",
  "visual_archetype": "<one of: dark-fintech | warm-domestic | clean-academic | high-contrast-bold | corporate-clean | muted-editorial | retro-futurism | neo-brutalism | editorial-magazine | scrollytelling | bento-3d | swiss-international>",
  "tone_target": "<one-sentence description of the visual tone>",
  "color_temperature": "<warm | cool | neutral | mixed>",
  "energy_level": "<low | medium | high>",
  "must_include_signals": ["<visual signals derived from script content — e.g. 'liquidation heatmap', 'kitchen close-ups', 'whiteboard explainer frames'>"],
  "exclude_styles": ["<styles to avoid for this domain>"],
  "fleet_owner_hint": "<Dexter | Memo | Sienna | Nano>",
  "stop_or_proceed": "PROCEED|STOP",
  "stop_reason": ""
}}
"""


def hermes_preroute(step1_brief: str = '', step2_script: str = '', notes: str = '') -> dict:
    try:
        from .learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=6)
    except Exception:
        learnings_text = '(learnings store unavailable)'
    payload = HERMES_TEMPLATE.format(
        step1_brief=(step1_brief or '(Step 1 brief not locked)')[:3000],
        step2_script=(step2_script or '(Step 2 script not locked)')[:1500],
        notes=(notes or '(none)')[:600],
        learnings=learnings_text,
    )
    raw = _call_ollama(payload, timeout=120)
    spec = _extract_json(raw)
    if spec:
        spec.setdefault('stop_or_proceed', 'PROCEED')
        spec.setdefault('topic_class', 'general')
        spec.setdefault('visual_archetype', 'clean-academic')
        spec.setdefault('color_temperature', 'neutral')
        spec.setdefault('energy_level', 'medium')
        spec.setdefault('must_include_signals', [])
        spec.setdefault('exclude_styles', [])
        return spec
    return {
        'topic_class': 'general',
        'domain': '',
        'visual_archetype': 'clean-academic',
        'tone_target': 'on-topic and clear',
        'color_temperature': 'neutral',
        'energy_level': 'medium',
        'must_include_signals': [],
        'exclude_styles': [],
        'fleet_owner_hint': 'Memo',
        'stop_or_proceed': 'PROCEED',
        'stop_reason': '',
        '_parse_error': 'Hermes returned non-JSON; using neutral fallback.',
    }


# ---------------------------------------------------------------------------
# Stage 2 — Visual outline / mood concept
# ---------------------------------------------------------------------------

OUTLINE_TEMPLATE = """You design the MOOD and visual concept for a 41-second video.
You produce an OUTLINE only — no specific colors or fonts yet. That comes next.

HERMES SPEC (treat as ground truth):
{hermes}

STEP 1 BRIEF (background — extract topic signals only):
{step1_brief}

STEP 2 SCRIPT (use to identify per-scene visual cues):
{step2_script}

USER NOTES:
{notes}

Output VALID JSON ONLY:
{{
  "concept_paragraph": "<3-5 sentence concept describing the overall visual feel>",
  "audience_visual_expectation": "<what someone in this domain expects to see>",
  "atmosphere_keywords": ["<5-8 atmospheric keywords>"],
  "reference_directions": ["<3-5 reference points by name — published examples or aesthetic directions>"],
  "emotional_arc": ["<6 entries, one per GDS section, each 2-4 words: 'curious unease', 'calm authority', etc>"]
}}
"""


def outline_pass(hermes: dict, step1_brief: str = '', step2_script: str = '',
                 notes: str = '') -> dict:
    payload = OUTLINE_TEMPLATE.format(
        hermes=json.dumps(hermes, indent=2)[:1200],
        step1_brief=(step1_brief or '')[:1800],
        step2_script=(step2_script or '')[:1200],
        notes=(notes or '(none)')[:600],
    )
    raw = _call_ollama(payload, timeout=150)
    spec = _extract_json(raw) or {}
    spec.setdefault('concept_paragraph', '')
    spec.setdefault('atmosphere_keywords', [])
    spec.setdefault('reference_directions', [])
    spec.setdefault('emotional_arc', [])
    return spec


# ---------------------------------------------------------------------------
# Stage 3 — Draft full design system
# ---------------------------------------------------------------------------

DESIGN_SYSTEM_TEMPLATE = """You produce a complete design system for a 41-second video.
The visual archetype, domain, and emotional arc are FIXED by Hermes + the outline.
Your job is to fill in the concrete colors, typography, motion, and per-scene treatments.

You MUST leverage the design skills + tools the user has installed locally — listed below.
Reference them by name in tools_recommended and asset_checklist where appropriate. The
design system should explicitly use installed tools rather than generic "use a design tool".

INSTALLED LOCAL DESIGN SKILLS (prioritized, hand-curated for design work):
{design_skills}

INSTALLED DESIGN / RENDERING TOOLS (probed via `which`):
{design_tools}

GITHUB DESIGN-SYSTEM REFERENCES (for inspiration, not blind copy):
{github_refs}

NEWLY DISCOVERED OSS TOOLS (from daily discovery scan — consider these for tools_recommended):
{oss_registry}

HERMES:
{hermes}

OUTLINE:
{outline}

STEP 2 SCRIPT (use to map per-scene treatments to the actual narration):
{step2_script}

USER NOTES:
{notes}

STRICT REQUIREMENTS:
- Color palette: 4-6 colors. Each MUST have a valid hex code (#XXXXXX) and a semantic role.
- Typography: a display face + a body face minimum. Optional mono. Real fonts available
  via Google Fonts or system stacks — name them precisely.
- Motion: list animation properties. Use ONLY compositor-friendly properties:
  transform, opacity, clip-path, filter, translate, scale, rotate. NEVER width / height /
  top / left / margin / padding / border / font-size for animation.
- Per-scene treatments: ALL 6 GDS sections (hook, thesis, evidence_1, evidence_2,
  implication, cta) must each get a 2-3 sentence visual treatment.
- Avoid cliches: no "centered hero with gradient blob", no "uniform card grid", no
  "minimal and clean" without concrete specifics, no "sleek modern cutting-edge".

Output VALID JSON ONLY:
{{
  "color_palette": [
    {{"role": "background", "hex": "#XXXXXX", "name": "...", "use": "..."}},
    {{"role": "primary",    "hex": "#XXXXXX", "name": "...", "use": "..."}},
    {{"role": "accent",     "hex": "#XXXXXX", "name": "...", "use": "..."}},
    ...
  ],
  "typography": {{
    "display":  {{"family": "...", "weight": "...", "use": "...", "fallback": "..."}},
    "body":     {{"family": "...", "weight": "...", "use": "...", "fallback": "..."}},
    "mono":     {{"family": "...", "weight": "...", "use": "...", "fallback": "..."}}
  }},
  "motion_principles": [
    {{"principle": "...", "properties_used": ["transform", "opacity", ...], "duration_ms": 300, "easing": "cubic-bezier(...)"}}
  ],
  "texture_atmosphere": "<2-3 sentences on grain, blur, depth, layering>",
  "scene_treatments": {{
    "hook":        "<2-3 sentence treatment for the Hook scene>",
    "thesis":      "<treatment for Thesis>",
    "evidence_1":  "<treatment for Evidence 1>",
    "evidence_2":  "<treatment for Evidence 2>",
    "implication": "<treatment for Implication>",
    "cta":         "<treatment for CTA>"
  }},
  "asset_checklist": ["<each asset that must be produced — fonts, icons, vectors, photography, etc>"],
  "tools_recommended": ["<concrete tools: Remotion, Canvas 2D, GSAP, Framer Motion, Manim, etc>"]
}}
"""


def draft_design_system(hermes: dict, outline: dict, step2_script: str = '',
                        notes: str = '', mode: str = 'fast',
                        harvest: dict | None = None) -> dict:
    harvest = harvest or {}
    payload = DESIGN_SYSTEM_TEMPLATE.format(
        hermes=json.dumps(hermes, indent=2)[:900],
        outline=json.dumps(outline, indent=2)[:1300],
        step2_script=(step2_script or '')[:1200],
        notes=(notes or '(none)')[:500],
        design_skills=json.dumps(harvest.get('design_skills', []), indent=2)[:2200],
        design_tools=json.dumps(harvest.get('design_tools', []), indent=2)[:1000],
        github_refs=json.dumps(harvest.get('github_refs', []), indent=2)[:1500],
        oss_registry=(harvest.get('oss_registry') or '(not yet populated)')[:1200],
    )
    if mode == 'deep' and _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
        text = _call_perplexity(payload)
        if text and not text.startswith('_('):
            spec = _extract_json(text)
            if spec:
                return spec
    raw = _call_ollama(payload, timeout=240)
    spec = _extract_json(raw) or {}
    return spec


# ---------------------------------------------------------------------------
# Stage 4 — Validators (now includes WCAG contrast audit)
# ---------------------------------------------------------------------------

HEX_RE = re.compile(r'^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$')


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int] | None:
    """Convert #RRGGBB or #RGB to (r, g, b). Returns None on bad input."""
    s = (hex_str or '').strip().lstrip('#')
    if len(s) == 3:
        s = ''.join(c * 2 for c in s)
    if len(s) != 6 or not all(c in '0123456789abcdefABCDEF' for c in s):
        return None
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance formula."""
    def _c(v: int) -> float:
        v_norm = v / 255.0
        return v_norm / 12.92 if v_norm <= 0.03928 else ((v_norm + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _c(r) + 0.7152 * _c(g) + 0.0722 * _c(b)


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    a = _hex_to_rgb(hex_a)
    b = _hex_to_rgb(hex_b)
    if not a or not b:
        return 0.0
    la = _relative_luminance(a)
    lb = _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def wcag_audit(palette: list[dict]) -> dict:
    """Run a WCAG 2.1 contrast audit across every meaningful pair in the palette.
    Reports the worst pair, the count of pairs failing the 4.5:1 body-text bar,
    and an overall grade."""
    colors = [(c.get('role', '?'), c.get('hex', '')) for c in (palette or [])
              if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', '')))]
    pairs: list[dict] = []
    for i, (role_a, hex_a) in enumerate(colors):
        for role_b, hex_b in colors[i + 1:]:
            ratio = _contrast_ratio(hex_a, hex_b)
            # WCAG bars: 4.5:1 = AA normal text, 3:1 = AA large text, 7:1 = AAA
            if ratio >= 7.0:
                level = 'AAA'
            elif ratio >= 4.5:
                level = 'AA'
            elif ratio >= 3.0:
                level = 'AA-large'
            else:
                level = 'FAIL'
            pairs.append({
                'a_role': role_a, 'a_hex': hex_a,
                'b_role': role_b, 'b_hex': hex_b,
                'ratio': ratio, 'level': level,
            })
    if not pairs:
        return {'pairs': [], 'fail_count': 0, 'grade': 'YELLOW',
                'worst_pair': None, 'best_pair': None,
                'note': 'No valid hex pairs to audit'}
    fail_count = sum(1 for p in pairs if p['level'] == 'FAIL')
    aa_count = sum(1 for p in pairs if p['level'] in ('AA', 'AAA'))
    worst = min(pairs, key=lambda p: p['ratio'])
    best = max(pairs, key=lambda p: p['ratio'])
    # Grade: GREEN if at least 1 AA pair AND no failures; YELLOW if some failures
    # but the bg/primary/accent triad is OK; RED if many failures.
    if fail_count == 0 and aa_count >= 1:
        grade = 'GREEN'
    elif fail_count <= 1:
        grade = 'YELLOW'
    else:
        grade = 'RED'
    return {
        'pairs': pairs,
        'fail_count': fail_count,
        'aa_or_better_count': aa_count,
        'worst_pair': worst,
        'best_pair': best,
        'grade': grade,
    }


def _validate_design_inner(ds: dict) -> dict:
    palette = ds.get('color_palette') or []
    typography = ds.get('typography') or {}
    motion = ds.get('motion_principles') or []
    scenes = ds.get('scene_treatments') or {}

    # Palette: 3-6 colors with valid hex
    valid_hex_count = sum(1 for c in palette if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', ''))))
    palette_size = len(palette)
    if valid_hex_count == palette_size and 4 <= palette_size <= 6:
        palette_grade = 'GREEN'
    elif palette_size >= 3 and valid_hex_count >= 3:
        palette_grade = 'YELLOW'
    else:
        palette_grade = 'RED'

    # Typography: display + body required
    has_display = bool((typography.get('display') or {}).get('family'))
    has_body = bool((typography.get('body') or {}).get('family'))
    typography_grade = 'GREEN' if (has_display and has_body) else ('YELLOW' if has_display or has_body else 'RED')

    # Motion: at least one principle, all properties_used must be compositor-friendly
    motion_props = []
    for m in motion:
        if isinstance(m, dict):
            motion_props.extend([str(p).lower() for p in (m.get('properties_used') or [])])
    layout_bound_used = [p for p in motion_props if p in LAYOUT_BOUND]
    if motion and not layout_bound_used:
        motion_grade = 'GREEN'
    elif motion and len(layout_bound_used) <= 1:
        motion_grade = 'YELLOW'
    else:
        motion_grade = 'RED'

    # Scene coverage: all 6 GDS sections must have non-empty treatment
    scenes_present = sum(1 for s in GDS_SECTIONS if scenes.get(s, '').strip())
    if scenes_present == 6:
        scene_grade = 'GREEN'
    elif scenes_present >= 4:
        scene_grade = 'YELLOW'
    else:
        scene_grade = 'RED'

    # Cliche check: searches concept paragraph + atmosphere keywords + treatments
    haystack = ' '.join([
        ds.get('texture_atmosphere', ''),
        ' '.join(scenes.values()) if isinstance(scenes, dict) else '',
        json.dumps(ds.get('atmosphere_keywords', [])),
    ]).lower()
    cliches_found = [c for c in BANNED_CLICHES if c in haystack]
    cliche_grade = 'GREEN' if not cliches_found else ('YELLOW' if len(cliches_found) <= 1 else 'RED')

    return {
        'palette_size': palette_size,
        'palette_valid_hex_count': valid_hex_count,
        'palette_grade': palette_grade,
        'has_display_font': has_display,
        'has_body_font': has_body,
        'typography_grade': typography_grade,
        'motion_layout_bound_used': layout_bound_used,
        'motion_grade': motion_grade,
        'scenes_present': scenes_present,
        'scenes_total': 6,
        'scene_grade': scene_grade,
        'cliches_found': cliches_found,
        'cliche_grade': cliche_grade,
    }


def validate_design(ds: dict) -> dict:
    """Public validator. Runs the structural validators then layers in the
    WCAG 2.1 contrast audit so the rating reflects accessibility too."""
    base = _validate_design_inner(ds)
    audit = wcag_audit(ds.get('color_palette') or [])
    base['wcag_audit'] = audit
    base['accessibility_grade'] = audit.get('grade', 'YELLOW')
    return base


# ---------------------------------------------------------------------------
# Multi-format export — Figma tokens, Tailwind, CSS vars, Remotion theme
# ---------------------------------------------------------------------------

def export_design_system(ds: dict) -> dict:
    """Generate downloadable representations of the design system in 4 formats
    so designers/devs can drop them straight into their tooling."""
    palette = ds.get('color_palette') or []
    typography = ds.get('typography') or {}
    motion = ds.get('motion_principles') or []

    def _slug(s: str) -> str:
        return re.sub(r'[^a-z0-9]+', '-', (s or '').lower()).strip('-') or 'item'

    # 1. Figma tokens (W3C Design Tokens spec)
    figma_tokens = {'$schema': 'https://design-tokens.org/schema/v1', 'color': {}, 'typography': {}, 'motion': {}}
    for c in palette:
        if not isinstance(c, dict) or not HEX_RE.match(str(c.get('hex', ''))):
            continue
        figma_tokens['color'][_slug(c.get('role', ''))] = {
            '$value': c['hex'], '$type': 'color',
            '$description': f"{c.get('name', '')} — {c.get('use', '')}",
        }
    for role in ('display', 'body', 'mono'):
        t = typography.get(role) or {}
        if t.get('family'):
            figma_tokens['typography'][role] = {
                '$value': {'fontFamily': t['family'], 'fontWeight': t.get('weight', '400')},
                '$type': 'typography',
                '$description': t.get('use', ''),
            }
    for i, m in enumerate(motion or []):
        if isinstance(m, dict) and m.get('principle'):
            figma_tokens['motion'][_slug(m['principle']) or f'motion_{i}'] = {
                '$value': {
                    'duration': f"{m.get('duration_ms', 300)}ms",
                    'easing': m.get('easing', 'ease-out'),
                    'properties': m.get('properties_used', []),
                },
                '$type': 'transition',
            }

    # 2. Tailwind config (CommonJS)
    tw_colors_lines = []
    for c in palette:
        if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', ''))):
            tw_colors_lines.append(f"        '{_slug(c.get('role', ''))}': '{c['hex']}',  // {c.get('name', '')} — {c.get('use', '')}")
    tw_fonts_lines = []
    for role in ('display', 'body', 'mono'):
        t = typography.get(role) or {}
        if t.get('family'):
            fallback = t.get('fallback') or ('serif' if role == 'body' else 'sans-serif' if role == 'display' else 'monospace')
            tw_fonts_lines.append(f"        {role}: ['{t['family']}', '{fallback}'],")
    tailwind_config = (
        "/** @type {import('tailwindcss').Config} */\n"
        "module.exports = {\n"
        "  theme: {\n"
        "    extend: {\n"
        "      colors: {\n" + '\n'.join(tw_colors_lines) + "\n      },\n"
        "      fontFamily: {\n" + '\n'.join(tw_fonts_lines) + "\n      },\n"
        "    },\n"
        "  },\n"
        "  plugins: [],\n"
        "};\n"
    )

    # 3. CSS custom properties
    css_lines = [':root {']
    for c in palette:
        if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', ''))):
            css_lines.append(f"  --color-{_slug(c.get('role', ''))}: {c['hex']};")
    for role in ('display', 'body', 'mono'):
        t = typography.get(role) or {}
        if t.get('family'):
            fallback = t.get('fallback') or ('serif' if role == 'body' else 'sans-serif' if role == 'display' else 'monospace')
            css_lines.append(f"  --font-{role}: '{t['family']}', {fallback};")
    if motion and isinstance(motion[0], dict):
        css_lines.append(f"  --duration-default: {motion[0].get('duration_ms', 300)}ms;")
        css_lines.append(f"  --easing-default: {motion[0].get('easing', 'ease-out')};")
    css_lines.append('}\n')
    css_vars = '\n'.join(css_lines)

    # 4. Remotion theme module
    remotion_colors_lines = [f"  {_slug(c.get('role', ''))}: '{c['hex']}',"
                             for c in palette
                             if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', '')))]
    remotion_fonts_lines = []
    for role in ('display', 'body', 'mono'):
        t = typography.get(role) or {}
        if t.get('family'):
            remotion_fonts_lines.append(f"  {role}: '{t['family']}, " + (t.get('fallback') or 'sans-serif') + "',")
    remotion_motion_lines = []
    for m in motion[:3]:
        if isinstance(m, dict):
            remotion_motion_lines.append(f"  {{ principle: '{m.get('principle', '')}', durationMs: {m.get('duration_ms', 300)}, easing: '{m.get('easing', 'ease-out')}' }},")
    remotion_theme = (
        "// Generated design system — drop into your Remotion src/theme.ts\n"
        "export const theme = {\n"
        "  colors: {\n" + '\n'.join(remotion_colors_lines) + "\n  },\n"
        "  fonts: {\n" + '\n'.join(remotion_fonts_lines) + "\n  },\n"
        "  motion: [\n" + '\n'.join(remotion_motion_lines) + "\n  ],\n"
        "} as const;\n"
    )

    return {
        'figma_tokens': json.dumps(figma_tokens, indent=2),
        'tailwind_config': tailwind_config,
        'css_vars': css_vars,
        'remotion_theme': remotion_theme,
    }


# ---------------------------------------------------------------------------
# Live HTML preview — renders the design system as an autoplay 41-second
# animated mockup that the user can SEE in a sandboxed iframe.
# ---------------------------------------------------------------------------

def render_live_preview(ds: dict, script: str = '', step1_brief: str = '') -> str:
    """Returns a self-contained HTML document showcasing the design system
    with the actual palette, typography, and motion principles applied to
    a 6-scene 41-second animated mockup."""
    palette = ds.get('color_palette') or []
    typography = ds.get('typography') or {}
    motion = ds.get('motion_principles') or []
    scenes = ds.get('scene_treatments') or {}

    # Build a CSS variable map from valid colors
    color_vars: dict = {}
    for c in palette:
        if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', ''))):
            role = (c.get('role') or 'color').lower().replace(' ', '-')
            color_vars[role] = c['hex']

    bg = color_vars.get('background', '#0A0E1A')
    primary = color_vars.get('primary', '#00D4FF')
    accent = color_vars.get('accent', color_vars.get('secondary', '#FFD700'))
    text_color = color_vars.get('text', '#E2E8F0')

    # Pick fonts
    display_font = (typography.get('display') or {}).get('family', 'Inter')
    body_font = (typography.get('body') or {}).get('family', 'Inter')
    mono_font = (typography.get('mono') or {}).get('family', 'JetBrains Mono')

    # Pull a primary motion principle for the mockup transitions
    primary_motion = motion[0] if motion else {}
    duration = primary_motion.get('duration_ms', 600)
    easing = primary_motion.get('easing', 'cubic-bezier(0.16, 1, 0.3, 1)')

    section_titles = {
        'hook': 'Hook',
        'thesis': 'Thesis',
        'evidence_1': 'Evidence I',
        'evidence_2': 'Evidence II',
        'implication': 'Implication',
        'cta': 'Call to Action',
    }

    # Build per-scene HTML — each renders the treatment text in the chosen
    # palette, with an animation that exercises the motion principles.
    scene_blocks = []
    for i, key in enumerate(['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']):
        treatment = (scenes.get(key) or '').strip() or '(no treatment)'
        scene_blocks.append(f'''
            <section class="scene scene-{i}" data-scene="{key}">
              <div class="scene-num">{i+1} / 6</div>
              <h2 class="scene-title">{section_titles[key]}</h2>
              <p class="scene-treatment">{treatment[:280].replace('"', '&quot;')}</p>
            </section>''')

    palette_swatches = []
    for c in palette:
        if isinstance(c, dict) and HEX_RE.match(str(c.get('hex', ''))):
            palette_swatches.append(
                f'<div class="swatch" style="background:{c["hex"]}">'
                f'<span style="font-family:{mono_font},monospace">{c["hex"]}</span>'
                f'<small>{c.get("role", "")}</small></div>'
            )

    google_fonts_param = '&family='.join(
        f.replace(' ', '+') + ':wght@400;600;700'
        for f in [display_font, body_font, mono_font]
        if f and f.lower() not in ('system-ui', 'sans-serif', 'serif', 'monospace')
    )
    google_fonts_link = (
        f'<link href="https://fonts.googleapis.com/css2?family={google_fonts_param}&display=swap" rel="stylesheet">'
        if google_fonts_param else ''
    )

    sf = 'system-ui, -apple-system, sans-serif'
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>Design System Preview</title>
{google_fonts_link}
<style>
  :root {{
    --bg: {bg};
    --primary: {primary};
    --accent: {accent};
    --text: {text_color};
    --font-display: '{display_font}', {sf};
    --font-body: '{body_font}', {sf};
    --font-mono: '{mono_font}', monospace;
    --motion-duration: {duration}ms;
    --motion-easing: {easing};
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 100%; height: 100%; background: var(--bg); color: var(--text); font-family: var(--font-body); overflow: hidden; }}
  .stage {{
    position: relative; width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
  }}
  .scene {{
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 32px;
    opacity: 0;
    transform: scale(0.96) translateY(20px);
    transition: opacity var(--motion-duration) var(--motion-easing),
                transform var(--motion-duration) var(--motion-easing);
  }}
  .scene.active {{ opacity: 1; transform: scale(1) translateY(0); }}
  .scene-num {{
    font-family: var(--font-mono);
    font-size: 11px; letter-spacing: 1px;
    color: var(--primary); opacity: 0.7;
    text-transform: uppercase; margin-bottom: 16px;
  }}
  .scene-title {{
    font-family: var(--font-display);
    font-size: clamp(28px, 5vw, 56px);
    font-weight: 700;
    color: var(--primary);
    margin-bottom: 18px;
    text-align: center;
    letter-spacing: -0.02em;
  }}
  .scene-treatment {{
    font-family: var(--font-body);
    font-size: clamp(14px, 1.6vw, 18px);
    line-height: 1.55;
    max-width: 640px;
    text-align: center;
    color: var(--text);
    opacity: 0.92;
  }}
  .meta {{
    position: absolute; top: 12px; left: 12px;
    display: flex; gap: 6px; flex-wrap: wrap;
    font-family: var(--font-mono); font-size: 9px; color: var(--text); opacity: 0.5;
  }}
  .meta span {{ background: rgba(255,255,255,0.08); padding: 3px 7px; border-radius: 4px; }}
  .progress {{
    position: absolute; bottom: 0; left: 0; height: 3px;
    background: var(--primary);
    width: 0%;
    transition: width 0.3s linear;
  }}
  .palette-strip {{
    position: absolute; bottom: 12px; left: 12px;
    display: flex; gap: 4px;
  }}
  .swatch {{
    width: 38px; height: 38px; border-radius: 6px;
    display: flex; align-items: flex-end; justify-content: center;
    font-size: 8px; padding: 3px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
  }}
  .swatch small {{ display: none; }}
  .replay {{
    position: absolute; top: 12px; right: 12px;
    font-family: var(--font-mono); font-size: 10px;
    background: var(--primary); color: var(--bg);
    border: none; padding: 6px 12px; border-radius: 6px;
    cursor: pointer; font-weight: 700;
  }}
  .accent-bar {{
    position: absolute; top: 0; left: 0; right: 0; height: 4px;
    background: linear-gradient(90deg, var(--primary), var(--accent));
  }}
</style>
</head><body>
<div class="stage">
  <div class="accent-bar"></div>
  <div class="meta">
    <span>{display_font}</span>
    <span>{body_font}</span>
    <span>{(primary_motion.get('principle') or 'no motion').lower()}</span>
    <span>{duration}ms</span>
  </div>
  {''.join(scene_blocks)}
  <div class="progress" id="progress"></div>
  <div class="palette-strip">{''.join(palette_swatches[:6])}</div>
  <button class="replay" onclick="replay()">↻ replay</button>
</div>
<script>
  // 41s total / 6 scenes ≈ 6.8s each
  const SCENE_MS = 6800;
  const TOTAL_MS = 41000;
  let timer = null;
  let progressTimer = null;
  function play() {{
    const scenes = document.querySelectorAll('.scene');
    let i = 0;
    scenes.forEach(s => s.classList.remove('active'));
    if (scenes[0]) scenes[0].classList.add('active');
    clearInterval(timer);
    clearInterval(progressTimer);
    const start = Date.now();
    progressTimer = setInterval(() => {{
      const pct = Math.min(100, ((Date.now()-start)/TOTAL_MS)*100);
      document.getElementById('progress').style.width = pct + '%';
      if (pct >= 100) clearInterval(progressTimer);
    }}, 100);
    timer = setInterval(() => {{
      i++;
      if (i >= scenes.length) {{ clearInterval(timer); return; }}
      scenes.forEach(s => s.classList.remove('active'));
      scenes[i].classList.add('active');
    }}, SCENE_MS);
  }}
  function replay() {{ play(); }}
  play();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Design-skills harvest — leverages every installed local design skill +
# probes design-relevant tools so the design system synthesis is biased
# toward proven, professional patterns instead of generic LLM defaults.
# ---------------------------------------------------------------------------

DESIGN_SKILL_TARGETS = [
    # Top-priority design skills (installed)
    'design', 'design-system', 'design-consultation', 'design-review',
    'frontend-design', 'ui-ux-pro-max', 'ui-styling', 'banner-design',
    # Visual/aesthetic specialty skills
    'liquid-glass-design', 'huashu-design',
    # Brand + voice skills
    'brand', 'brand-voice', 'brand-voice-guide',
    # Slides / presentation skills
    'slides', 'frontend-slides',
    # Anthropic design skills
    'canvas-design', 'algorithmic-art', 'theme-factory',
    'brand-guidelines', 'web-artifacts-builder',
    # Animation / video skills relevant to motion design
    'remotion-video-creation', 'manim-video',
]


def harvest_design_skills(keywords: list[str] | None = None) -> list[dict]:
    """Scan ~/.claude/skills + plugins for skills that can professionalize
    the visual direction. Prioritizes hand-curated DESIGN_SKILL_TARGETS list,
    then falls back to keyword overlap for less-obvious matches."""
    SKILLS_DIR = HOME / '.claude' / 'skills'
    PLUGINS_DIR = HOME / '.claude' / 'plugins'
    keywords_lower = [k.lower() for k in (keywords or [])]
    matches: list[dict] = []
    seen = set()

    def consider(skill_dir: Path, origin: str):
        if not skill_dir.is_dir():
            return
        name = skill_dir.name
        if name in seen:
            return
        skill_md = skill_dir / 'SKILL.md'
        if not skill_md.exists():
            return
        try:
            text = skill_md.read_text(errors='ignore')
        except Exception:
            return
        # Priority match
        is_target = name in DESIGN_SKILL_TARGETS
        priority = DESIGN_SKILL_TARGETS.index(name) if is_target else 999
        # Keyword overlap fallback
        if not is_target and keywords_lower:
            haystack = (text + ' ' + name).lower()
            if not any(kw in haystack for kw in keywords_lower):
                return
        elif not is_target:
            # No keywords supplied AND not a curated target → skip
            return
        desc = ''
        for line in text.splitlines()[:30]:
            line = line.strip()
            if line.lower().startswith('description:'):
                desc = line.split(':', 1)[1].strip().strip('"\'')
                break
        seen.add(name)
        matches.append({
            'name': name,
            'description': desc[:280],
            'origin': origin,
            'invoke': f'/{name}' if origin == 'user-skills' else f'/{origin.replace("plugin:", "")}:{name}',
            'priority': priority,
            'is_curated': is_target,
        })

    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            consider(skill_dir, 'user-skills')
    if PLUGINS_DIR.exists():
        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            skills_root = plugin_dir / 'skills'
            if not skills_root.exists():
                continue
            for skill_dir in sorted(skills_root.iterdir()):
                consider(skill_dir, f'plugin:{plugin_dir.name}')
    matches.sort(key=lambda m: (m['priority'], m['name']))
    return matches[:18]


def harvest_design_tools() -> list[dict]:
    """Probe for design / animation / rendering tools actually installed."""
    candidates = [
        ('figma', 'design system editor'),
        ('sketch', 'macOS design tool'),
        ('framer', 'motion design + prototyping'),
        ('node', 'Remotion / Three.js / GSAP runtime'),
        ('pnpm', 'fast Node package manager'),
        ('npx', 'invoke design-system CLIs'),
        ('manim', 'mathematical/scientific motion graphics'),
        ('blender', '3D scene production'),
        ('imagemagick', 'image conversion'),
        ('ffmpeg', 'composite + export'),
        ('rsvg-convert', 'SVG → raster pipelines'),
        ('inkscape', 'SVG editor / CLI export'),
        ('gimp', 'raster editor'),
        ('python3.13', 'manim / matplotlib runtime'),
    ]
    found: list[dict] = []
    for cli, purpose in candidates:
        path = shutil.which(cli)
        if path:
            found.append({'name': cli, 'path': path, 'purpose': purpose})
    return found


def harvest_github_design_refs(keywords: list[str]) -> list[dict]:
    """gh search repos for design-system / motion / video aesthetics references
    biased by the user's domain. Always uses gh CLI."""
    if not keywords:
        return []
    queries = [
        ' '.join(keywords[:2]) + ' design system',
        ' '.join(keywords[:2]) + ' motion graphics',
    ]
    out: list[dict] = []
    seen = set()
    for query in queries:
        try:
            res = subprocess.run(
                ['gh', 'search', 'repos',
                 '--json', 'fullName,description,stargazersCount,url,language,updatedAt',
                 '--sort', 'stars', '--limit', '6', '--', query],
                capture_output=True, text=True, timeout=20,
            )
            if res.returncode == 0 and res.stdout.strip():
                for h in json.loads(res.stdout):
                    if isinstance(h, dict) and h.get('fullName') and h['fullName'] not in seen:
                        seen.add(h['fullName'])
                        h['_query'] = query
                        out.append(h)
        except Exception:
            continue
    return out[:10]


def _harvest_oss_registry() -> str:
    """Read the daily OSS registry and return design-relevant tools as text."""
    try:
        from .discovery import registry_for_steps
        return registry_for_steps(steps=['step3_visual'], max_tools=15)
    except Exception:
        return '(OSS registry unavailable)'


def harvest_step3(hermes: dict) -> dict:
    """Run all design harvesters in parallel, including the OSS registry."""
    keywords = []
    if hermes.get('domain'):
        keywords.append(hermes['domain'].split(' ')[0])
    if hermes.get('visual_archetype'):
        keywords.append(hermes['visual_archetype'].replace('-', ' '))
    keywords.extend([str(s).split()[0] for s in (hermes.get('must_include_signals') or [])][:2])
    keywords = [k for k in keywords if k]

    out: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(harvest_design_skills, keywords): 'design_skills',
            pool.submit(harvest_design_tools): 'design_tools',
            pool.submit(harvest_github_design_refs, keywords): 'github_refs',
            pool.submit(_harvest_oss_registry): 'oss_registry',
        }
        for fut in as_completed(futures):
            try:
                out[futures[fut]] = fut.result()
            except Exception as e:
                out[futures[fut]] = [{'error': str(e)}]
    return out


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review (visual-design lenses)
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Dev — render feasibility reviewer',
        'lens_template': 'Can this design system actually be implemented in Remotion / Canvas 2D / CSS within a 41-second timeline budget? Flag any motion principle that demands layout-bound properties. Check that the per-scene treatments compile down to <5 keyframe transitions each. Domain context: {domain}.',
    },
    'memo': {
        'role': 'PM — visual consistency reviewer',
        'lens_template': 'Does the design system hold across all 6 GDS scenes? Any scene whose treatment breaks the palette, typography, or motion principles? Is the asset_checklist realistic for the schedule? Domain context: {domain}.',
    },
    'sienna': {
        'role': 'Domain Specialist — authenticity reviewer',
        'lens_template': 'You review for the specific domain: {domain}. Does the visual archetype actually match how content in this field looks today? Flag any visual that feels off-domain (e.g., a fintech-dashboard look on a cooking video). Check brand-voice fit if a brand is implied.',
    },
    'nano': {
        'role': 'Agent Creator — engagement / hook punch reviewer',
        'lens_template': 'How strong is the Hook scene visually? Will it hold a viewer past 3 seconds? Is the CTA scene visually distinct enough to register as a call to action? Does the emotional arc actually progress, or does the energy stay flat?',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent_name} — {role}. Lens: {lens}.

Review THIS proposed design system for the user's video. Speak only in your domain. Be specific and brief.

DESIGN SYSTEM (JSON):
{design_system}

VALIDATORS (current grades):
{validators}

Output Markdown ONLY using these exact sections:

### {agent_name} — what to fix
2-3 sharp bullets. Each names a SPECIFIC element and what to change.

### {agent_name} — verdict
One line: GREEN-LIGHT / YELLOW-LIGHT / RED-LIGHT + 1-sentence reason.

### {agent_name} — if I owned this
1-2 lines: the very next concrete edit.
"""


def _review_one(agent: str, ds: dict, validators: dict, hermes: dict) -> dict:
    info = FLEET_REVIEWERS[agent]
    lens = info['lens_template'].format(domain=hermes.get('domain') or '(unspecified)')
    payload = FLEET_REVIEW_TEMPLATE.format(
        agent_name=agent.capitalize(),
        role=info['role'],
        lens=lens,
        design_system=json.dumps(ds, indent=2)[:3500],
        validators=json.dumps(validators, indent=2)[:600],
    )
    text = _call_ollama(payload, timeout=90)
    return {'agent': agent, 'role': info['role'], 'review': text.strip()}


def fleet_review(ds: dict, validators: dict, hermes: dict) -> list[dict]:
    reviews: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_review_one, a, ds, validators, hermes): a for a in FLEET_REVIEWERS}
        for fut in as_completed(futures):
            try:
                reviews.append(fut.result())
            except Exception as e:
                reviews.append({'agent': futures[fut], 'review': f'_(review failed: {e})_'})
    order = list(FLEET_REVIEWERS.keys())
    reviews.sort(key=lambda r: order.index(r['agent']) if r['agent'] in order else 99)
    return reviews


# ---------------------------------------------------------------------------
# Quality rating
# ---------------------------------------------------------------------------

def compute_quality(ds: dict, validators: dict, fleet_reviews: list[dict],
                    convergence_passes: int) -> dict:
    score = 5.0
    reasons: list[str] = []

    grades = [validators.get(k) for k in ('palette_grade', 'typography_grade', 'motion_grade', 'scene_grade', 'cliche_grade')]
    red_grades = sum(1 for g in grades if g == 'RED')
    yellow_grades = sum(1 for g in grades if g == 'YELLOW')
    if red_grades:
        score -= 1.5 * red_grades
        reasons.append(f'{red_grades} validator(s) RED')
    if yellow_grades:
        score -= 0.4 * yellow_grades
        reasons.append(f'{yellow_grades} validator(s) YELLOW')
    if validators.get('palette_grade') != 'GREEN':
        reasons.append(f"Palette: {validators.get('palette_size')} colors / {validators.get('palette_valid_hex_count')} valid hex")
    if validators.get('motion_grade') != 'GREEN' and validators.get('motion_layout_bound_used'):
        reasons.append(f"Motion uses layout-bound props: {validators['motion_layout_bound_used']}")
    if validators.get('scene_grade') != 'GREEN':
        reasons.append(f"Scene coverage: {validators.get('scenes_present')}/6 sections")
    if validators.get('cliches_found'):
        reasons.append(f"Cliches detected: {validators['cliches_found']}")

    verdicts = {'GREEN': 0, 'YELLOW': 0, 'RED': 0}
    for r in fleet_reviews or []:
        m = re.search(r'(GREEN|YELLOW|RED)-?LIGHT', r.get('review', ''), re.I)
        if m:
            verdicts[m.group(1).upper()] += 1
    if verdicts['RED'] >= 2:
        score -= 2.0
        reasons.append(f'{verdicts["RED"]} fleet RED')
    elif verdicts['RED'] == 1:
        score -= 1.0
        reasons.append('1 fleet RED (single-veto)')
    elif verdicts['YELLOW'] >= 2:
        score -= 0.5
        reasons.append(f'{verdicts["YELLOW"]} fleet YELLOW')
    if verdicts['GREEN'] == 4 and red_grades == 0:
        reasons.append('🟢 All 4 fleet GREEN + all hard validators GREEN')

    if convergence_passes >= 2:
        score -= 0.5
        reasons.append(f'{convergence_passes} convergence passes')
    elif convergence_passes == 1:
        score -= 0.25

    score = max(1.0, min(5.0, round(score * 2) / 2))
    if score >= 5.0:
        label = '🟢 Perfect design system — ship to scene production'
    elif score >= 4.0:
        label = '🟡 Strong system — refine to 5★ before Step 4'
    elif score >= 3.0:
        label = '🟡 Mixed — refine before advancing'
    elif score >= 2.0:
        label = '🟠 Weak — substantial gaps; iterate further'
    else:
        label = '🔴 Insufficient — re-prompt or check infra'

    return {
        'stars': score, 'label': label, 'reasons': reasons,
        'advance_ok': score >= 5.0,
        'fleet_verdicts': verdicts,
        'validator_grades': dict(zip(['palette', 'typography', 'motion', 'scene', 'cliche'], grades)),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_step3(step1_brief: str = '', step2_script: str = '', mode: str = 'fast',
              prior_design: str = '', notes: str = '',
              max_convergence: int = 2,
              project: str = 'default') -> dict:
    started = time.time()
    stage_times: dict = {}

    # Stage 1: Hermes
    t = time.time()
    hermes = hermes_preroute(step1_brief=step1_brief, step2_script=step2_script, notes=notes)
    stage_times['hermes_preroute'] = round(time.time() - t, 1)
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'stopped': True, 'hermes': hermes,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # Stage 2a + 2b: outline + design-skills harvest in parallel.
    # The harvest probes installed local design skills + tools + GitHub refs
    # so the next stage can name specific tools instead of generic placeholders.
    harvest: dict = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        outline_fut = pool.submit(outline_pass, hermes, step1_brief, step2_script, notes)
        harvest_fut = pool.submit(harvest_step3, hermes)
        t = time.time()
        outline = outline_fut.result()
        stage_times['visual_outline'] = round(time.time() - t, 1)
        try:
            harvest = harvest_fut.result(timeout=30)
        except Exception as e:
            harvest = {'error': str(e)}

    # Stage 3: full design system draft (now sees the installed skills + tools)
    t = time.time()
    design = draft_design_system(hermes, outline, step2_script=step2_script,
                                 notes=notes, mode=mode, harvest=harvest)
    stage_times['design_draft'] = round(time.time() - t, 1)

    # Stage 4: validate
    validators = validate_design(design)

    # Stage 5: fleet review with auto convergence on RED
    convergence_passes = 0
    fleet_reviews: list[dict] = []
    while convergence_passes <= max_convergence:
        t = time.time()
        fleet_reviews = fleet_review(design, validators, hermes)
        stage_times[f'fleet_review_pass_{convergence_passes+1}'] = round(time.time() - t, 1)
        red = sum(1 for r in fleet_reviews if re.search(r'RED-?LIGHT', r.get('review', ''), re.I))
        if red == 0 or convergence_passes >= max_convergence:
            break
        convergence_passes += 1
        critique = '\n\n'.join(r.get('review', '') for r in fleet_reviews
                               if re.search(r'RED-?LIGHT', r.get('review', ''), re.I))
        rewrite_prompt = (
            f'Revise this design system to address the RED-LIGHT critiques below. '
            f'Keep the visual archetype, keep validators green, keep all 6 scene treatments.\n\n'
            f'CRITIQUES:\n{critique[:2500]}\n\n'
            f'CURRENT DESIGN SYSTEM:\n{json.dumps(design)[:4000]}\n\n'
            f'Output ONLY the revised JSON.'
        )
        t = time.time()
        raw = _call_ollama(rewrite_prompt, timeout=240)
        revised = _extract_json(raw)
        if revised:
            design = revised
            validators = validate_design(design)
        stage_times[f'rewrite_pass_{convergence_passes}'] = round(time.time() - t, 1)

    rating = compute_quality(design, validators, fleet_reviews, convergence_passes)

    try:
        from .scoring import lock_step_from_run
        _reds = sum(1 for r in fleet_reviews if 'RED' in (r.get('review') or '').upper())
        lock_step_from_run(
            project=project, step=3,
            fleet={'summary': {'reds': _reds, 'greens': 0, 'yellows': 0}},
            stars=rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=rating.get('label', ''),
        )
    except Exception:
        pass

    try:
        from .skill_db import register_skill
        _prompt = (step2_script or step1_brief or '')[:500]
        _summary = f"step3 visual · {(rating.get('label') or '')[:80]}"
        _excerpt = {
            'palette_count': len((design.get('color_palette') or design.get('colors') or [])),
            'has_typography': bool(design.get('typography')),
            'fleet_red_count': sum(1 for r in fleet_reviews if 'RED' in (r.get('review') or '').upper()),
        }
        register_skill(
            step=3, prompt=_prompt,
            stars=rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=3, prompt=_prompt, summary=_summary,
                          result_excerpt=_excerpt, stars=rating.get('stars', 0.0))
    except Exception:
        pass

    return {
        'step1_brief_used': bool(step1_brief),
        'step2_script_used': bool(step2_script),
        'hermes': hermes,
        'outline': outline,
        'harvest': harvest,
        'design_system': design,
        'validators': validators,
        'fleet_reviews': fleet_reviews,
        'convergence_passes': convergence_passes,
        'quality_rating': rating,
        'mode': mode,
        'iteration': bool(prior_design or notes),
        'stage_times': stage_times,
        'elapsed_seconds': round(time.time() - started, 1),
    }


# ---------------------------------------------------------------------------
# Advice generator
# ---------------------------------------------------------------------------

ADVICE_TEMPLATE = """You are a design coach. The user produced this Step 3 design system.
It scored {stars}/5 — below the 5★ threshold. Write FOCUSED REFINEMENT NOTES that, when fed
back into the iteration loop, will trigger a re-draft addressing the weaknesses.

CURRENT QUALITY:
- Stars: {stars}
- Reasons:
{reasons}

VALIDATOR GRADES:
{validators}

FLEET REVIEW:
{reviews}

CURRENT DESIGN SYSTEM:
{design}

Output JSON ONLY:
{{
  "diagnosis": "<1-2 sentence diagnosis>",
  "focused_notes": "<3-5 sentence note that addresses every red-light + big yellow flag>",
  "specific_fixes": ["<concrete element-level fixes — 'change accent from #00D4FF to a warmer hex', etc>"],
  "expected_lift": "<+0.5 to +1.5 stars>"
}}
"""


def step3_advise(result: dict) -> dict:
    rating = result.get('quality_rating', {}) or {}
    validators = result.get('validators', {}) or {}
    reviews = result.get('fleet_reviews', []) or []
    reviews_block = ''
    for r in reviews[:4]:
        reviews_block += f'\n--- {r.get("agent","?").upper()} ---\n{r.get("review","")[:800]}\n'
    payload = ADVICE_TEMPLATE.format(
        stars=rating.get('stars', '?'),
        reasons='\n'.join(f'  • {r}' for r in (rating.get('reasons') or [])) or '  (none)',
        validators=json.dumps(rating.get('validator_grades', {}), indent=2),
        reviews=reviews_block[:4000],
        design=json.dumps(result.get('design_system', {}))[:3000],
    )
    raw = _call_ollama(payload, timeout=120)
    spec = _extract_json(raw)
    if spec:
        spec.setdefault('focused_notes', '')
        spec.setdefault('specific_fixes', [])
        spec.setdefault('diagnosis', '')
        spec.setdefault('expected_lift', '')
        return spec
    return {
        'diagnosis': 'Auto-fallback advice (model returned non-JSON).',
        'focused_notes': '\n'.join(f'Address: {r}' for r in (rating.get('reasons') or [])),
        'specific_fixes': [],
        'expected_lift': '+0.5 stars',
    }


# ---------------------------------------------------------------------------
# Post-success auto-research
# ---------------------------------------------------------------------------

POST_RESEARCH_TEMPLATE = """A Step 3 design system just got locked at {stars} stars. Distill
learnings for FUTURE design system runs. Output VALID JSON ONLY:

LOCKED DESIGN SYSTEM:
{design}

QUALITY:
{rating}

USER NOTES on advance:
{user_notes}

{{
  "what_worked": ["<concrete patterns from THIS system that produced a high score>"],
  "what_failed": ["<concrete patterns the fleet flagged or validators caught>"],
  "palette_lessons": ["<color choices that worked or didn't in this domain>"],
  "typography_lessons": ["<font pairing observations>"],
  "motion_pitfalls": ["<animations that risked layout-bound or render-cost issues>"],
  "next_video_recommendations": ["<concrete suggestions for the NEXT design system>"]
}}
"""


def step3_post_research(result: dict, user_notes: str = '') -> dict:
    rating = result.get('quality_rating') or {}
    payload = POST_RESEARCH_TEMPLATE.format(
        stars=rating.get('stars', '?'),
        design=json.dumps(result.get('design_system', {}))[:3000],
        rating=json.dumps(rating, indent=2)[:600],
        user_notes=(user_notes or '(none)')[:600],
    )
    raw = _call_ollama(payload, timeout=180)
    spec = _extract_json(raw) or {}
    record = {
        'kind': 'step3_postmortem',
        'stars': rating.get('stars'),
        'topic_class': (result.get('hermes') or {}).get('topic_class'),
        'visual_archetype': (result.get('hermes') or {}).get('visual_archetype'),
        'fleet_verdicts': rating.get('fleet_verdicts'),
        'convergence_passes': result.get('convergence_passes'),
        'what_worked': spec.get('what_worked', []),
        'what_failed': spec.get('what_failed', []),
        'palette_lessons': spec.get('palette_lessons', []),
        'typography_lessons': spec.get('typography_lessons', []),
        'motion_pitfalls': spec.get('motion_pitfalls', []),
        'next_video_recommendations': spec.get('next_video_recommendations', []),
        'user_notes': user_notes,
    }
    try:
        from .learnings import record_learning
        record_learning(record)
    except Exception:
        pass
    return record
