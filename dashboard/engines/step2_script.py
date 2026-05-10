#!/usr/bin/env python3.13
"""Step 2 — Narrative Script engine (GDS Framework).

Turns the locked Step 1 research brief + a small data block (numbers /
facts the user wants in the script) into a TTS-ready 90-word narration
following the GDS structure: Hook → Thesis → Evidence×2 → Implication → CTA.

Pipeline (same shape as Step 1, retuned for creative writing):
  Stage 1 — HERMES PRE-ROUTE (project context for script writing)
  Stage 2 — GDS PRE-PASS    (6-section outline with key points each)
  Stage 3 — DRAFT V1        (free-form first pass)
  Stage 4 — POLISH PASSES   (phonetic conversion → word-count trim → runtime)
  Stage 5 — FLEET REVIEW    (Dexter/Memo/Sienna/Nano with creative lenses)
  Convergence loop          (re-draft if any specialist red-lights)

Hard validators on the final script:
  • Word count 88–95 (yellow at 80–87 / 96–105, red outside)
  • Runtime estimate 38–43s at ~150 wpm (yellow at 35–37 / 44–46, red outside)
  • All numbers spelled phonetically
  • No banned chars (em-dash variants, smart quotes that confuse some TTS)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HOME = Path.home()
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'
PROJECT_ROOT = Path(__file__).resolve().parents[2]

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_MODEL = os.environ.get('STEP2_LOCAL_MODEL', 'qwen2.5:7b')
DEEP_MODEL = os.environ.get('STEP2_DEEP_MODEL', 'sonar-pro')

# Default targets — derived from the current Bitcoin video showcase (40s).
# These act as the fallback when length_seconds is not passed in. The real
# targets are now COMPUTED PER-RUN via compute_targets(length_seconds).
TARGET_WORDS_MIN = 88
TARGET_WORDS_MAX = 95
TARGET_RUNTIME_MIN = 38.0
TARGET_RUNTIME_MAX = 43.0
WPM = 150.0  # natural TTS pace at en_US-lessac-medium / Piper

BANNED_CHARS = ['—', '–', '‘', '’', '“', '”', '…']


def compute_targets(length_seconds: int = 40) -> dict:
    """Length-aware target computation.

    Scales word count, runtime, and section allocations to the requested
    video length. The current Bitcoin showcase is 40s/90 words — same
    schema, dynamic numbers.

    A 15-min documentary needs ~2,062 words, not 90. Without this scaling
    every downstream step (audio, subtitles, render) gets the wrong duration
    and the gold-button loops will never converge.
    """
    length_seconds = max(20, min(int(length_seconds or 40), 1800))
    # Sweet spot: ~150 WPM at conversational pace; pad ±5% for natural variance.
    target_words = round(length_seconds * (WPM / 60.0) * 0.92)  # 0.92 fudge for breath/pauses
    word_pad = max(3, round(target_words * 0.04))   # ±4% on word count
    runtime_pad = max(2.0, round(length_seconds * 0.05, 1))  # ±5% on runtime

    # Number of GDS sections scales with length: 6 for short, 9-12 for documentary.
    if length_seconds <= 60:
        n_sections = 6  # Hook / Thesis / Evidence×2 / Implication / CTA
    elif length_seconds <= 200:
        n_sections = 8  # + Backstory + Conflict
    elif length_seconds <= 500:
        n_sections = 10  # + Breakthrough + Evidence-3
    else:
        n_sections = 12  # full documentary arc

    return {
        'length_seconds': length_seconds,
        'words_min': target_words - word_pad,
        'words_max': target_words + word_pad,
        'words_target': target_words,
        'runtime_min': round(length_seconds - runtime_pad, 1),
        'runtime_max': round(length_seconds + runtime_pad, 1),
        'runtime_target': float(length_seconds),
        'wc_target_str': f'{target_words - word_pad}-{target_words + word_pad}',
        'rt_target_str': f'{round(length_seconds - runtime_pad, 1)}-{round(length_seconds + runtime_pad, 1)}s',
        'n_sections': n_sections,
        'wpm': WPM,
    }


# ---------------------------------------------------------------------------
# Env / key resolution (mirrors Step 1)
# ---------------------------------------------------------------------------

def _load_fleet_env() -> dict:
    env: dict[str, str] = {}
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
# LLM helpers (lazy import from step1_research to avoid duplication)
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
    backend = (os.environ.get('STEP2_LLM_BACKEND') or 'openrouter').strip().lower()
    if backend in {'openrouter', 'external'}:
        routed = _call_openrouter(prompt, timeout=timeout)
        if routed:
            return routed
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


def _call_openrouter(prompt: str, model: str | None = None, timeout: int = 240) -> str:
    key = _key('OPENROUTER_API_KEY', 'OPENROUTER_KEY', 'DLS_OPENROUTER_API_KEY')
    if not key:
        return ''
    model = model or os.environ.get('STEP2_OPENROUTER_MODEL') or os.environ.get('OPENROUTER_MODEL') or 'anthropic/claude-sonnet-4.6'
    body = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'provider': {
            'order': ['Anthropic', 'OpenAI', 'MoonshotAI', 'Z.AI'],
            'allow_fallbacks': True,
            'require_parameters': False,
        },
    }
    try:
        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=json.dumps(body).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'http://127.0.0.1:8766',
                'X-Title': 'Zmarty Video Pipeline Step 2',
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        choices = data.get('choices') or []
        if not choices:
            return ''
        return (choices[0].get('message') or {}).get('content', '').strip()
    except Exception as e:
        return f'_(OpenRouter error: {e})_'


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
        if not choices:
            return ''
        return choices[0].get('message', {}).get('content', '').strip()
    except Exception as e:
        return f'_(Perplexity error: {e})_'


def _call_openclaude(prompt: str, timeout: int = 360) -> str:
    """Route inference through Dan's local openclaude CLI (Claude Code with
    DavidAi ECC settings — currently backed by GLM-5.1).

    Used for private-topic script drafting. GLM-5.1 has 200K context and
    frontier reasoning; for a 15-min documentary script (~2,000 words) it
    produces dramatically richer narration than qwen2.5:7b. Falls back to
    Ollama on any failure.
    """
    import subprocess
    # Pass the prompt via stdin to avoid CLI startup noise leaking into
    # the prompt string. Redirect stderr away from stdout so any "Warning:
    # no stdin data received in 3s" or login chatter doesn't get embedded.
    cmd = [
        '/bin/zsh', '-i', '-c',
        'openclaude --print --output-format text "$1"',
        '_',
        prompt,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, check=False)
        text = (out.stdout or '').strip()
        if not text or out.returncode != 0:
            err = (out.stderr or '')[:200]
            return f'_(openclaude failed: rc={out.returncode}, err={err})_'
        # Strip any Warning/info chatter from stderr that may have bled in
        # (older Claude Code versions emit "Warning: no stdin..." even on -p)
        text = re.sub(r'^Warning:[^\n]*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*proceeding without it\.[^\n]*\n?', '', text, flags=re.MULTILINE)
        return text.strip()
    except subprocess.TimeoutExpired:
        return f'_(openclaude timed out after {timeout}s)_'
    except Exception as e:
        return f'_(openclaude error: {e})_'


# ---------------------------------------------------------------------------
# Local-context harvester (mirrors Step 1 — for private-topic detection)
# ---------------------------------------------------------------------------

# Weighted trigger detection. The previous "any token wins" rule was too
# aggressive — a public Bitcoin marketing video that says "Mention zmarty.me"
# was wrongly classified private and routed to GLM-5.1, which then lost the
# Bitcoin context entirely. Now we score: a single ambiguous brand-name token
# (zmarty, dexter, memo, …) is not enough. Need a clear lab signal OR multiple
# product/team mentions clustered together.
LOCAL_TRIGGER_WEIGHTS = {
    # HIGH-signal — almost-always means Dan's private context
    'danslab': 1.0,
    'dansidanutz': 1.0,
    'kryptostack': 1.0,
    'mywork': 1.0,
    'irise coin': 1.0,
    "player's poker": 1.0,
    'cluj': 1.0,
    # MEDIUM — internal product/infra names
    'nervix': 0.6,
    'openclaw': 0.6,
    'semeclaw': 0.6,
    'paperclip': 0.6,
    'mac studio': 0.6,
    'mac mini': 0.6,
    'tailscale': 0.5,
    # LOW — ambiguous (could be a generic crypto/dev mention)
    'zmartychat': 0.4,
    'zmarty': 0.3,        # 'zmarty.me' alone is a brand mention, not lab-internal
    'dans ': 0.4,         # only when "Dans" used as personal/lab name
    'dan ': 0.3,          # very common word
    # AMBIGUOUS team-member names — only count when multiple appear together
    'dexter': 0.2,
    'memo': 0.2,
    'sienna': 0.2,
    'nano': 0.2,
}

# Score threshold for "this is a private/lab-internal topic"
PRIVATE_TOPIC_THRESHOLD = 1.0


def _is_private_topic(prompt: str) -> bool:
    """Detect if the topic is internal/private to Dan's lab — triggers
    openclaude routing + local-context-aware draft prompt.

    Uses a weighted score (rather than any-token-wins) so a single brand-name
    mention like "Mention zmarty.me" in a Bitcoin video does NOT trigger private
    routing. Needs clear lab signals (e.g. "DansLab" + "Nervix") to flip True.
    """
    prompt_lc = (prompt or '').lower()
    score = 0.0
    for token, weight in LOCAL_TRIGGER_WEIGHTS.items():
        if token in prompt_lc:
            score += weight
    return score >= PRIVATE_TOPIC_THRESHOLD


def _strip_to_text(raw: str) -> str:
    """Strip markdown code fences / preamble. Accepts the model output and
    returns just the script body."""
    s = raw.strip()
    s = re.sub(r'^```(?:[a-zA-Z]+)?\s*|\s*```\s*$', '', s, flags=re.MULTILINE)
    return s.strip()


# ---------------------------------------------------------------------------
# Stage 1 — Hermes pre-route (script-writing variant)
# ---------------------------------------------------------------------------

HERMES_TEMPLATE = """You are Hermes orchestrating a script-writing step. The user wants a
TTS-ready 90-word narration script. The TOPIC AND DOMAIN are entirely up to the user —
this engine is domain-agnostic. Examples it must serve equally well: crypto explainers,
YouTube tutorials, product launches, educational content, recipe videos, B2B SaaS demos,
documentaries, news recaps, etc.

UNIVERSAL CONSTRAINTS (apply to every script):
- Tone: confident, data/fact-driven, no hype words
- Target runtime: 38-43 seconds at ~150 wpm
- TTS engine: Piper-compatible. ALL numbers MUST be spelled phonetically.
- Banned tokens: em-dash, en-dash, smart quotes (TTS may mispronounce)

YOUR JOB: extract the topic + brand + URL + domain from the user's input. Do NOT
inject any brand or domain context that the user did not supply.

USER'S RAW INPUT (data points / topic / constraints — TREAT AS GROUND TRUTH for what to write about):
{user_input}

STEP 1 RESEARCH BRIEF (background only — extract topic signals; do NOT assume the brief's domain is the script's domain):
{step1_brief}

USER NOTES on previous iteration (may be empty):
{notes}

Output VALID JSON ONLY:
{{
  "topic_class": "<auto-detected from user input — examples: crypto-news, product-explainer, tutorial, educational, recipe, news-recap, software-demo, etc>",
  "domain": "<the actual subject area: 'cryptocurrency trading', 'cooking', 'machine learning', 'photography', 'remote work', etc — derived from user input>",
  "brand_name": "<brand mentioned in user input, or empty string if no brand>",
  "brand_url": "<URL mentioned in user input, or empty string>",
  "tone_target": "<adapted to topic_class — confident for finance, warm for cooking, technical for software, etc>",
  "key_data_points": ["<must-include numbers/facts in priority order, drawn from user input>"],
  "exclude": ["<what to leave out — domain-appropriate filters>"],
  "phonetic_targets": ["<numbers/brand names/URLs needing phonetic spelling — derived from THIS user input only>"],
  "fleet_owner_hint": "<Dexter for technical/dev topics | Memo for business/PM topics | Sienna for finance/crypto/markets | Nano for automation/agents/AI topics>",
  "stop_or_proceed": "PROCEED|STOP",
  "stop_reason": ""
}}
"""


def hermes_preroute(user_input: str, step1_brief: str = '', notes: str = '') -> dict:
    payload = HERMES_TEMPLATE.format(
        user_input=(user_input or '')[:1500],
        step1_brief=(step1_brief or '(no Step 1 brief locked yet)')[:3500],
        notes=(notes or '(none)')[:600],
    )
    raw = _call_ollama(payload, model=LOCAL_MODEL, timeout=120)
    cleaned = _strip_to_text(raw)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            spec = json.loads(m.group(0))
            spec.setdefault('stop_or_proceed', 'PROCEED')
            spec.setdefault('topic_class', 'other')
            spec.setdefault('key_data_points', [])
            spec.setdefault('phonetic_targets', [])
            return spec
        except Exception:
            pass
    return {
        'topic_class': 'other',
        'domain': '',
        'brand_name': '',
        'brand_url': '',
        'tone_target': 'data-driven, confident, no hype',
        'key_data_points': [],
        'exclude': [],
        'phonetic_targets': [],
        'fleet_owner_hint': 'Memo',
        'stop_or_proceed': 'PROCEED',
        'stop_reason': '',
        '_parse_error': 'Hermes returned non-JSON; using fallback (no domain assumed).',
    }


# ---------------------------------------------------------------------------
# Stage 2 — GDS pre-pass: outline the 6 sections
# ---------------------------------------------------------------------------

GDS_TEMPLATE = """You are designing the OUTLINE (not the script) for a {wc_target}-word video narration ({rt_target} runtime).
You apply the GDS framework: Hook → Thesis → Evidence → Implication → CTA, scaled to {n_sections} sections for this length.

USER REQUEST / DATA:
{user_input}

HERMES SPEC (project context):
{hermes}

STEP 1 BRIEF (background, don't restate):
{step1_brief}

TARGET: {wc_target} words total · {rt_target} runtime · {n_sections} sections · TTS-ready (phonetic numbers, no em-dash).

For SHORT (6 sections, ≤60s): hook / thesis / evidence_1 / evidence_2 / implication / cta.
For MEDIUM (8-10 sections, 60-500s): + backstory + conflict + breakthrough + evidence_3.
For LONG (12 sections, >500s — full documentary arc): cold_open / protagonist / world / mission / team / architecture / conflict / breakthrough / evidence / metrics / vision / cta.

Output VALID JSON ONLY with the outline. Each section gets a one-line goal and 1-3 key facts to include.
Word allocations across the {n_sections} sections should SUM TO APPROXIMATELY {wc_words} words.

For 6 sections, use this schema:
{{
  "hook":        {{"goal": "...", "facts": ["..."], "word_allocation": 18}},
  "thesis":      {{"goal": "...", "facts": ["..."], "word_allocation": 14}},
  "evidence_1":  {{"goal": "...", "facts": ["..."], "word_allocation": 16}},
  "evidence_2":  {{"goal": "...", "facts": ["..."], "word_allocation": 14}},
  "implication": {{"goal": "...", "facts": ["..."], "word_allocation": 12}},
  "cta":         {{"goal": "...", "facts": ["..."], "word_allocation": 16}},
  "core_promise": "<one-sentence summary of what this script delivers>",
  "audience": "<who watches this>"
}}

For 8/10/12 sections, ADD additional named sections following the long-form list above. Word allocations
should be roughly even (e.g. 12 sections × ~165 words each for a 15-min documentary).
"""


def gds_prepass(user_input: str, hermes: dict, step1_brief: str = '',
                targets: dict | None = None) -> dict:
    if targets is None:
        targets = compute_targets(40)
    payload = GDS_TEMPLATE.format(
        user_input=(user_input or '')[:1500],
        hermes=json.dumps(hermes, indent=2)[:1200],
        step1_brief=(step1_brief or '')[:2500],
        wc_target=targets['wc_target_str'],
        rt_target=targets['rt_target_str'],
        n_sections=targets['n_sections'],
        wc_words=targets['words_target'],
    )
    raw = _call_ollama(payload, model=LOCAL_MODEL, timeout=120)
    cleaned = _strip_to_text(raw)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            spec = json.loads(m.group(0))
            # Always ensure the canonical 6 sections exist (fallback for short videos)
            for k in ['hook', 'thesis', 'evidence_1', 'evidence_2', 'implication', 'cta']:
                spec.setdefault(k, {'goal': '', 'facts': [], 'word_allocation': max(8, targets['words_target'] // targets['n_sections'])})
            return spec
        except Exception:
            pass
    # Fallback outline scales the per-section allocation to target length
    per_section = max(10, targets['words_target'] // 6)
    return {
        'hook':        {'goal': 'Open with the most arresting data point', 'facts': [], 'word_allocation': per_section + 3},
        'thesis':      {'goal': 'State the core market position', 'facts': [], 'word_allocation': per_section - 1},
        'evidence_1':  {'goal': 'First supporting data point', 'facts': [], 'word_allocation': per_section + 1},
        'evidence_2':  {'goal': 'Second supporting data point', 'facts': [], 'word_allocation': per_section - 1},
        'implication': {'goal': 'What this means for the viewer', 'facts': [], 'word_allocation': per_section - 3},
        'cta':         {'goal': 'Direct to ZmartyChat or relevant CTA', 'facts': [], 'word_allocation': per_section + 1},
        'core_promise': '',
        'audience': 'general',
        '_parse_error': 'GDS outline returned non-JSON; using length-aware fallback.',
    }


# ---------------------------------------------------------------------------
# Stage 3-4 — Drafting + polish passes
# ---------------------------------------------------------------------------

DRAFT_TEMPLATE = """You write a {wc_target}-word video narration script ({rt_target} runtime, {n_sections} sections) following the GDS framework.
The TOPIC, BRAND, URL, and DOMAIN come ENTIRELY from the user's input via Hermes.
Do NOT introduce any topic, brand, or product the user did not specify.

GDS OUTLINE (must follow):
{outline}

HERMES PROJECT CONTEXT (derived from THIS user's prompt):
- Topic class: {topic_class}
- Domain: {domain}
- Tone: {tone}
- Brand name (if any): {brand_name}
- Brand URL (if any): {brand_url}
- Phonetic targets (must be spelled out, NOT digits): {phonetic_targets}
- Exclude: {exclude}

USER NOTES (priority guidance):
{notes}

STRICT REQUIREMENTS:
1. Word count: {wc_target} (HARD). Count every word. Aim for the middle of the range.
2. All numbers spelled phonetically: "70,000" → "seventy thousand", "$3.5B" → "three point five billion dollars".
3. No em-dash, en-dash, or smart quotes — use plain ASCII only.
4. {n_sections} paragraphs separated by blank lines, in the order specified by the outline.
5. If a brand was supplied above, mention it with EXACT capitalization. If a URL was supplied, spell it phonetically (".com" → "dot com", ".me" → "dot me", ".io" → "dot eye oh", etc).
6. If no brand was supplied, write a topic-only script — do NOT invent a brand or product name.
7. Confident, on-topic tone — no hype words ("revolutionary", "amazing", "incredible", "game-changer").

Output ONLY the script text. No preamble, no headings, no JSON, no markdown."""


def draft_script(outline: dict, hermes: dict, notes: str = '',
                 mode: str = 'fast', targets: dict | None = None) -> str:
    if targets is None:
        targets = compute_targets(40)
    payload = DRAFT_TEMPLATE.format(
        outline=json.dumps(outline, indent=2)[:1800],
        tone=hermes.get('tone_target', 'confident, on-topic, no hype'),
        topic_class=hermes.get('topic_class', 'general'),
        domain=hermes.get('domain', '(unspecified)'),
        brand_name=hermes.get('brand_name', '') or '(none)',
        brand_url=hermes.get('brand_url', '') or '(none)',
        phonetic_targets=', '.join(hermes.get('phonetic_targets', []) or []) or '(none — auto-detect from text)',
        exclude=', '.join(hermes.get('exclude', []) or []) or '(none)',
        notes=(notes or '(none)')[:600],
        wc_target=targets['wc_target_str'],
        rt_target=targets['rt_target_str'],
        n_sections=targets['n_sections'],
    )
    # Drafting is the heaviest stage — for long videos (>5min) timeout needs scaling
    timeout = max(180, int(targets['length_seconds'] * 0.4) + 60)

    # PRIVATE TOPIC ROUTING — when the user_input or hermes flagged a private
    # topic (DansLab/Nervix/team/etc.), use openclaude (GLM-5.1) for the draft.
    # qwen2.5:7b is fine for short Bitcoin scripts but underperforms on
    # 2,000-word documentaries that reference real people/projects.
    is_private = bool(hermes.get('_is_private_topic'))
    if is_private:
        via = (os.environ.get('STEP2_PRIVATE_VIA') or 'auto').lower()
        if via != 'ollama':
            text = _call_openclaude(payload, timeout=timeout + 60)
            if text and not text.startswith('_('):
                return _strip_to_text(text)
            if via == 'openclaude':
                # Forced openclaude — surface the failure rather than silently fallback
                return text  # already starts with '_(' so caller sees the error

    if mode == 'deep' and _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
        text = _call_perplexity(payload)
        if text and not text.startswith('_('):
            return _strip_to_text(text)
    return _strip_to_text(_call_ollama(payload, timeout=timeout))


POLISH_TEMPLATE = """You polish a video narration script for TTS. Fix these in priority order, but DO NOT change the meaning or topic:

1. Replace any banned chars (em-dash, en-dash, smart quotes) with plain ASCII.
2. Spell out ALL numbers phonetically (e.g. "$3.5B" → "three point five billion dollars", "145,837" → "one hundred forty-five thousand eight hundred thirty-seven", "2024" → "twenty twenty four").
3. Replace ANY URL with phonetic form: ".com" → "dot com", ".me" → "dot me", ".io" → "dot eye oh", ".ai" → "dot a i", ".net" → "dot net", etc — so TTS reads it cleanly.
4. Trim or expand to land in {wc_target} words. Current count: {word_count}.
5. ABSOLUTELY CRITICAL: output EXACTLY {n_sections} paragraphs separated by blank lines (one empty line between each paragraph). NOT one big paragraph. Each paragraph is one beat of the GDS arc.
6. Do NOT introduce new brand names, products, or topics the original draft did not include.

EXAMPLE FORMAT (for 6 sections):

[paragraph 1: hook]

[paragraph 2: thesis]

[paragraph 3: evidence one]

[paragraph 4: evidence two]

[paragraph 5: implication]

[paragraph 6: cta]

CURRENT DRAFT:
{draft}

Output ONLY the polished script text — exactly {n_sections} paragraphs, separated by blank lines. No preamble, no headings, no JSON, no numbering."""


def _enforce_paragraph_breaks(text: str, n_sections: int) -> str:
    """Post-processing safety net: if the model returns a wall of text instead
    of {n_sections} paragraphs, split at sentence boundaries to approximate
    the right structure. Better than RED-grade output forever.
    """
    if not text or not text.strip():
        return text
    # Already has enough paragraph breaks?
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) >= n_sections - 1:  # close enough — 1-off is fine
        return text
    # Split at sentence boundaries and group into n_sections roughly-equal chunks
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) < n_sections:
        return text  # not enough sentences to split — leave as-is
    # Distribute sentences across n_sections groups
    per_group = max(1, len(sentences) // n_sections)
    extra = len(sentences) % n_sections
    grouped: list[str] = []
    idx = 0
    for i in range(n_sections):
        size = per_group + (1 if i < extra else 0)
        chunk = ' '.join(sentences[idx:idx + size])
        if chunk:
            grouped.append(chunk)
        idx += size
    return '\n\n'.join(grouped)


def _enforce_word_count(text: str, words_min: int, words_max: int) -> str:
    """Hard truncation safety net for word count. Local 7B-8B models do not
    reliably hit precise word-count targets — they swing 30-130 words for an
    88-95 target. Better to deterministically clip to the upper bound than
    leave a RED-grade output.

    On overshoot: trim PROPORTIONALLY across all paragraphs (preserving section
    structure). Removes one sentence from the longest paragraph at each step,
    so all sections survive. Falls back to dropping last paragraph only if a
    single paragraph would otherwise become empty.
    On undershoot: leave alone — adding fake words would degrade the script.
    """
    if not text or not text.strip():
        return text
    wc = count_words(text)
    if wc <= words_max:
        return text  # Either in range or undershot — don't pad
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    # Iteratively pop the longest sentence from the longest paragraph
    safety = 50
    while paragraphs and count_words('\n\n'.join(paragraphs)) > words_max and safety > 0:
        safety -= 1
        # Find paragraph with the most words that still has >1 sentence
        sentence_lists = []
        for p in paragraphs:
            sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', p) if s.strip()]
            sentence_lists.append(sents)
        # Pick the paragraph with the highest total word count that has ≥2 sentences
        best_idx = -1
        best_words = 0
        for i, sents in enumerate(sentence_lists):
            if len(sents) < 2:
                continue
            wc_p = sum(count_words(s) for s in sents)
            if wc_p > best_words:
                best_words = wc_p
                best_idx = i
        if best_idx == -1:
            # No paragraph has ≥2 sentences; drop last single-sentence paragraph
            paragraphs.pop()
            continue
        # Remove the longest sentence from that paragraph
        sents = sentence_lists[best_idx]
        # Drop the longest sentence (most likely the most expendable detail)
        longest_sent_idx = max(range(len(sents)), key=lambda j: count_words(sents[j]))
        sents.pop(longest_sent_idx)
        paragraphs[best_idx] = ' '.join(sents)
    return '\n\n'.join(p for p in paragraphs if p.strip()) if paragraphs else text


def polish_pass(draft: str, targets: dict | None = None) -> str:
    if targets is None:
        targets = compute_targets(40)
    wc = count_words(draft)
    payload = POLISH_TEMPLATE.format(
        draft=draft[:4000],
        word_count=wc,
        wc_target=targets['wc_target_str'],
        n_sections=targets['n_sections'],
    )
    polished = _strip_to_text(_call_ollama(payload, timeout=180))
    # Safety net 1 — if the model still returns a single paragraph, split it.
    polished = _enforce_paragraph_breaks(polished, targets['n_sections'])
    # Safety net 2 — if the model overshot the word count, trim deterministically.
    polished = _enforce_word_count(polished, targets['words_min'], targets['words_max'])
    return polished


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def count_words(text: str) -> int:
    if not text:
        return 0
    # Treat hyphenated words as one
    return len(re.findall(r"\b[\w'-]+\b", text))


def estimate_runtime_seconds(text: str) -> float:
    return round(60.0 * count_words(text) / WPM, 1)


def find_digit_runs(text: str) -> list[str]:
    """Return any sequence of digits the script still contains. These should
    have been spelled phonetically; their presence is a fail signal."""
    return re.findall(r'\b\d[\d,.]*\b', text or '')


def find_banned_chars(text: str) -> list[str]:
    return [c for c in BANNED_CHARS if c in (text or '')]


def validate_script(text: str, targets: dict | None = None) -> dict:
    """Validate against length-aware targets.

    targets dict comes from compute_targets(length_seconds). Falls back to
    legacy 40-second defaults when not provided so existing callers stay
    backward-compatible during the rollout.
    """
    if targets is None:
        targets = compute_targets(40)
    wc = count_words(text)
    rt = estimate_runtime_seconds(text)
    digits = find_digit_runs(text)
    banned = find_banned_chars(text)
    section_count = len([p for p in (text or '').split('\n\n') if p.strip()])

    def grade(value: float, lo: float, hi: float, soft_pad: float):
        if lo <= value <= hi:
            return 'GREEN'
        if (lo - soft_pad) <= value <= (hi + soft_pad):
            return 'YELLOW'
        return 'RED'

    # Word-count soft pad scales with target — 8 is correct for 90 words but
    # too tight for 2000. Use ~9% of target (rounded), minimum 6.
    word_soft_pad = max(6, round((targets['words_max'] - targets['words_min']) * 1.5))
    runtime_soft_pad = max(3, round((targets['runtime_max'] - targets['runtime_min']) * 0.6, 1))

    word_grade = grade(wc, targets['words_min'], targets['words_max'], soft_pad=word_soft_pad)
    rt_grade = grade(rt, targets['runtime_min'], targets['runtime_max'], soft_pad=runtime_soft_pad)
    phonetic_grade = 'GREEN' if not digits else ('YELLOW' if len(digits) <= 2 else 'RED')
    banned_grade = 'GREEN' if not banned else 'RED'

    # Section count expectation scales too — 6 for short, up to 12 for long.
    expected_n = targets['n_sections']
    if abs(section_count - expected_n) <= 1:
        section_grade = 'GREEN'
    elif abs(section_count - expected_n) <= 3:
        section_grade = 'YELLOW'
    else:
        section_grade = 'RED'

    return {
        'word_count': wc,
        'word_count_target': targets['wc_target_str'],
        'word_count_grade': word_grade,
        'runtime_seconds': rt,
        'runtime_target': targets['rt_target_str'],
        'runtime_grade': rt_grade,
        'digits_found': digits,
        'phonetic_grade': phonetic_grade,
        'banned_chars': banned,
        'banned_chars_grade': banned_grade,
        'section_count': section_count,
        'section_count_target': expected_n,
        'section_grade': section_grade,
        'length_seconds': targets['length_seconds'],
    }


# ---------------------------------------------------------------------------
# Stage 5 — Fleet review (creative-writing lenses)
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Dev — data accuracy reviewer',
        'lens_template': 'Verify EVERY number in the script is correct against the Step 1 brief and the user\'s data points. Flag any contradiction. Check that phonetic spellings of numbers are unambiguous (e.g., "fifteen hundred" vs "one thousand five hundred"). Check unit consistency for the topic ({domain}).',
    },
    'memo': {
        'role': 'PM — narrative flow reviewer',
        'lens_template': 'Does the GDS structure hold? Is each transition clear? Is the CTA strong and topic-appropriate for {domain}? Does it feel rushed or padded? Any sentence that does not earn its words. Does the runtime budget feel realistic at TTS pace?',
    },
    'sienna': {
        'role': 'Domain Specialist — topic accuracy + tone reviewer',
        'lens_template': 'You are reviewing for the specific domain: {domain}. Check that vocabulary, jargon, and conventions match how practitioners in that field actually talk. Flag any factual errors about the subject. Flag any cringe / generic / off-domain phrases. Check brand-voice fit for "{brand_name}" if a brand is present.',
    },
    'nano': {
        'role': 'Agent Creator — hook + CTA reviewer',
        'lens_template': 'How strong is the first sentence as a hook? Does the CTA actually drive a specific action (visiting "{brand_url}" if a URL is present, or the next concrete step the user needs the viewer to take)? Is there a specific reason to act NOW? What would a 7-second drop-off look like?',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent_name} — {role}. Lens: {lens}.

TOPIC KIND: {topic_kind}
{topic_kind_hint}

Review THIS {wc_target}-word video script for the user. Speak only in your domain. Be specific and brief.

SCRIPT (with validators):
Word count: {wc} (target {wc_target}). Runtime estimate: {rt}s (target {rt_target}).

{script}

Output Markdown ONLY using these exact sections:

### {agent_name} — what to fix
2-3 sharp bullets. Each bullet names a SPECIFIC sentence or word and what to change.

### {agent_name} — verdict
One line: GREEN-LIGHT / YELLOW-LIGHT / RED-LIGHT + 1-sentence reason.

### {agent_name} — if I owned this
1-2 lines: the very next edit you would make.
"""

PRIVATE_TOPIC_HINT_S2 = (
    "PRIVATE — script is about Dan's Lab internals (DansLab/Nervix/team/architecture). "
    "Public web search returning empty for these terms is EXPECTED. The script should reference "
    "the actual people and products by name. Do NOT RED-LIGHT for 'unfamiliar names' or "
    "'missing public references' — those are real internal entities."
)
PUBLIC_TOPIC_HINT_S2 = (
    "PUBLIC — general or commercial topic. Apply normal rigor: the script should be factually "
    "accurate, well-sourced where stats appear, and free of hype words."
)


def _review_one(agent: str, script: str, validators: dict, hermes: dict | None = None) -> dict:
    info = FLEET_REVIEWERS[agent]
    hermes = hermes or {}
    is_private = bool(hermes.get('_is_private_topic'))
    # Format the per-agent lens with the user's actual domain/brand from Hermes
    lens = info.get('lens') or info.get('lens_template', '').format(
        domain=hermes.get('domain') or '(unspecified domain)',
        brand_name=hermes.get('brand_name') or '(no brand)',
        brand_url=hermes.get('brand_url') or '(no URL)',
    )
    payload = FLEET_REVIEW_TEMPLATE.format(
        agent_name=agent.capitalize(),
        role=info['role'],
        lens=lens,
        topic_kind='PRIVATE/INTERNAL' if is_private else 'PUBLIC',
        topic_kind_hint=PRIVATE_TOPIC_HINT_S2 if is_private else PUBLIC_TOPIC_HINT_S2,
        wc=validators.get('word_count', 0),
        rt=validators.get('runtime_seconds', 0),
        wc_target=validators.get('word_count_target', '88-95'),
        rt_target=validators.get('runtime_target', '38-43s'),
        script=(script or '')[:3500],
    )
    text = _call_ollama(payload, timeout=90)
    return {'agent': agent, 'role': info['role'], 'review': text.strip()}


def fleet_review(script: str, validators: dict, hermes: dict | None = None) -> list[dict]:
    reviews: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_review_one, a, script, validators, hermes): a for a in FLEET_REVIEWERS}
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

def compute_quality(script: str, validators: dict, fleet_reviews: list[dict],
                    convergence_passes: int) -> dict:
    score = 5.0
    reasons: list[str] = []

    # Hard validator penalties
    grades = [
        validators.get('word_count_grade'),
        validators.get('runtime_grade'),
        validators.get('phonetic_grade'),
        validators.get('banned_chars_grade'),
        validators.get('section_grade'),
    ]
    red_grades = sum(1 for g in grades if g == 'RED')
    yellow_grades = sum(1 for g in grades if g == 'YELLOW')
    if red_grades:
        score -= 1.5 * red_grades
        reasons.append(f'{red_grades} hard validator RED-grade')
    if yellow_grades:
        score -= 0.4 * yellow_grades
        reasons.append(f'{yellow_grades} hard validator YELLOW-grade')

    if validators.get('word_count_grade') != 'GREEN':
        reasons.append(f"Word count {validators.get('word_count')} not in {validators.get('word_count_target')}")
    if validators.get('runtime_grade') != 'GREEN':
        reasons.append(f"Runtime {validators.get('runtime_seconds')}s not in {validators.get('runtime_target')}")
    if validators.get('digits_found'):
        reasons.append(f"Digits still in script (should be phonetic): {validators['digits_found'][:5]}")
    if validators.get('banned_chars'):
        reasons.append(f"Banned chars present: {validators['banned_chars']}")

    # Fleet verdicts
    verdicts = {'GREEN': 0, 'YELLOW': 0, 'RED': 0}
    for r in fleet_reviews or []:
        m = re.search(r'(GREEN|YELLOW|RED)-?LIGHT', r.get('review', ''), re.I)
        if m:
            verdicts[m.group(1).upper()] += 1
    if verdicts['RED'] >= 2:
        score -= 2.0
        reasons.append(f'{verdicts["RED"]} fleet reviewers RED-lighted')
    elif verdicts['RED'] == 1:
        score -= 1.0
        reasons.append('1 fleet reviewer RED-lighted (single-veto)')
    elif verdicts['YELLOW'] >= 2:
        score -= 0.5
        reasons.append(f'{verdicts["YELLOW"]} fleet reviewers raised yellow flags')
    if verdicts['GREEN'] == 4 and red_grades == 0:
        reasons.append('🟢 All 4 fleet reviewers green-lit + all hard validators green')

    if convergence_passes >= 2:
        score -= 0.5
        reasons.append(f'Took {convergence_passes} convergence passes')
    elif convergence_passes == 1:
        score -= 0.25
        reasons.append('Took 1 convergence pass')

    score = max(1.0, min(5.0, round(score * 2) / 2))
    if score >= 5.0:
        label = '🟢 Perfect script — ship to TTS'
    elif score >= 4.0:
        label = '🟡 Strong script — refine to 5★ before Step 3'
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
        'validator_grades': grades,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def consolidate_with_review(script: str, reviews: list[dict]) -> str:
    if not reviews:
        return script
    md = script.rstrip()
    md += '\n\n---\n\n## 👥 Fleet Review (post-script)\n\n'
    for r in reviews:
        md += r.get('review', '_(no review)_').strip() + '\n\n'
    return md


# ---------------------------------------------------------------------------
# Open-source harvest (mirrors Step 1's pattern for script-writing references)
# ---------------------------------------------------------------------------

import shutil
import subprocess

def harvest_github_scripts(keywords: list[str]) -> list[dict]:
    """Search GitHub for narration / scriptwriting / video-narration repos that
    can serve as templates or reference. Always uses gh CLI."""
    if not keywords:
        return []
    candidates = []
    queries = [
        ' '.join(keywords[:2]) + ' video script narration',
        ' '.join(keywords[:1]) + ' narration template',
        'GDS framework narration script',
    ]
    for query in queries[:2]:
        try:
            out = subprocess.run(
                ['gh', 'search', 'repos',
                 '--json', 'fullName,description,stargazersCount,url,language,updatedAt',
                 '--sort', 'stars', '--limit', '6', '--', query],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode == 0 and out.stdout.strip():
                hits = json.loads(out.stdout)
                if hits:
                    for h in hits:
                        if isinstance(h, dict):
                            h['_query'] = query
                            candidates.append(h)
                    break
        except Exception:
            continue
    # Dedupe by fullName
    seen = set()
    unique = []
    for c in candidates:
        if c.get('fullName') and c['fullName'] not in seen:
            seen.add(c['fullName'])
            unique.append(c)
    return unique[:8]


def harvest_local_writing_skills(keywords: list[str]) -> list[dict]:
    """Scan ~/.claude/skills + plugins for skills relevant to script writing,
    brand voice, or content engines."""
    SKILLS_DIR = HOME / '.claude' / 'skills'
    PLUGINS_DIR = HOME / '.claude' / 'plugins'
    targets = ['content-engine', 'brand-voice', 'brand', 'article-writing',
               'banner-design', 'video-clone-agent', 'remotion-video-creation',
               'content-hash', 'manim-video', 'seedance-all', 'video-editing',
               'ui-demo']
    keywords_lower = [k.lower() for k in (keywords or [])]
    matches: list[dict] = []
    seen_names = set()

    def consider(skill_dir: Path, origin: str):
        if not skill_dir.is_dir():
            return
        name = skill_dir.name
        if name in seen_names:
            return
        skill_md = skill_dir / 'SKILL.md'
        if not skill_md.exists():
            return
        try:
            text = skill_md.read_text(errors='ignore')
        except Exception:
            return
        haystack = (text + ' ' + name).lower()
        # Match if name is a known target OR keyword overlaps
        match_score = sum(1 for t in targets if t in name)
        if not match_score:
            match_score = sum(1 for kw in keywords_lower if kw in haystack)
        if match_score == 0:
            return
        desc = ''
        for line in text.splitlines()[:30]:
            line = line.strip()
            if line.lower().startswith('description:'):
                desc = line.split(':', 1)[1].strip().strip('"\'')
                break
        seen_names.add(name)
        matches.append({
            'name': name,
            'description': desc[:240],
            'origin': origin,
            'invoke': f"/{name}",
        })

    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            consider(skill_dir, 'user-skills')
            if len(matches) >= 12:
                return matches
    if PLUGINS_DIR.exists():
        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            skills_root = plugin_dir / 'skills'
            if not skills_root.exists():
                continue
            for skill_dir in sorted(skills_root.iterdir()):
                consider(skill_dir, f'plugin:{plugin_dir.name}')
                if len(matches) >= 12:
                    return matches
    return matches


def harvest_tts_tools() -> list[dict]:
    """Inventory TTS engines + media tools that are actually installed."""
    candidates = [
        ('piper', 'local neural TTS (en_US-lessac-medium baseline)'),
        ('espeak-ng', 'phoneme-aware fallback TTS'),
        ('whisper-cli', 'TTS verification via round-trip transcription'),
        ('ffmpeg', 'audio mux + atempo runtime correction'),
        ('sox', 'audio post-processing'),
        ('manim', 'mathematical animations sync'),
    ]
    found = []
    for cli, purpose in candidates:
        path = shutil.which(cli)
        if path:
            found.append({'name': cli, 'path': path, 'purpose': purpose})
    return found


def harvest_step2(hermes: dict) -> dict:
    """Run the script-writing harvest in parallel: GitHub references, local
    skills, TTS tool inventory."""
    # Build keyword set from hermes spec
    keywords = []
    if hermes.get('topic_class'):
        keywords.append(hermes['topic_class'])
    keywords += [str(t).split()[0] for t in (hermes.get('phonetic_targets') or [])][:3]
    if not keywords:
        keywords = ['narration', 'video', 'script']

    out: dict = {}
    def _oss_registry_for_step2() -> str:
        try:
            from .discovery import registry_for_steps
            return registry_for_steps(
                steps=['step2_script', 'step5_audio'],
                categories=['audio-tts', 'editing'],
                max_tools=12,
            )
        except Exception as e:
            return f'(OSS registry unavailable: {e})'

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(harvest_github_scripts, keywords): 'github_scripts',
            pool.submit(harvest_local_writing_skills, keywords): 'writing_skills',
            pool.submit(harvest_tts_tools): 'tts_tools',
            pool.submit(_oss_registry_for_step2): 'oss_registry',
        }
        for fut in as_completed(futures):
            try:
                out[futures[fut]] = fut.result()
            except Exception as e:
                out[futures[fut]] = [{'error': str(e)}]
    return out


# ---------------------------------------------------------------------------
# Production document — rich narrative output beyond the bare script
# ---------------------------------------------------------------------------

NARRATIVE_DOC_TEMPLATE = """You produce a PRODUCTION DOCUMENT around a 90-word video narration script.
This document goes to the voice actor (or TTS director), the editor, and the localization team.
It is the bridge between the script and the rendered video.

USER ASK:
{user_input}

LOCKED SCRIPT (do NOT rewrite it — describe how to perform and produce it):
{script}

GDS OUTLINE:
{outline}

HERMES SPEC:
{hermes}

Output well-structured Markdown with these EXACT headings, in this order. Be thorough — this
is the documentation, not the script. Aim for 400-600 words across all sections combined.

## Voice Direction
A confident director's note for the TTS / voice actor. Tone, energy curve, breath points.
Mark which words to stress. Note any line that needs a specific emotional read.

## Pacing Map
Section-by-section: which scene each paragraph anchors to, the target seconds, and the
emphasis word per sentence. Map to the 6 video scenes if possible.

## Alternative Hook Openings
Provide 3 ALTERNATIVE opening sentences (12-18 words each) the user can A/B-test against
the locked Hook. Each alt should be in the same tone but try a different angle (data-led,
question-led, contrarian).

## Production Notes
Visual cues that should align with each spoken line. Color/motion cues. Where the
liquidation heatmap pulses, where the chart morphs, where the CTA card lands.

## Localization Risk
Terms that may not translate cleanly to other markets (idioms, brand-specific phrases,
US-centric numbers). One bullet per risk + a suggested neutral alternative.

## TTS Tweaks
Specific phonetic adjustments for the chosen engine (Piper en_US-lessac-medium).
Words known to mispronounce ("longs" → "lungs" risk; "ZmartyChat" → "C-Marty Chat" risk).
SSML/phoneme suggestions where appropriate.

## Quality Sign-Off Checklist
A 5-7 item checklist the editor uses before exporting the final video.
"""


def produce_narrative_doc(script: str, outline: dict, hermes: dict,
                          user_input: str = '') -> str:
    if (os.environ.get('STEP2_NARRATIVE_DOC') or 'off').strip().lower() != 'on':
        sections = outline.get('sections') if isinstance(outline, dict) else None
        n_sections = len(sections) if isinstance(sections, list) else 0
        return (
            'Production notes\n\n'
            f'Topic: {(user_input or hermes.get("domain") or "video")[:240]}\n'
            f'Sections: {n_sections or "derived from script"}\n'
            'Voice: clear, authoritative, TTS-ready pacing.\n'
            'Edit: align each scene to the matching narration beat; preserve visual continuity.\n'
            'QA: verify final MP4 duration, audio stream, subtitles, and 1920x1080 H.264 output.'
        )
    payload = NARRATIVE_DOC_TEMPLATE.format(
        user_input=(user_input or '')[:600],
        script=(script or '')[:2000],
        outline=json.dumps(outline, indent=2)[:1500],
        hermes=json.dumps(hermes, indent=2)[:800],
    )
    return _strip_to_text(_call_ollama(payload, model=LOCAL_MODEL, timeout=60))


# ---------------------------------------------------------------------------
# Post-success auto-research — feeds future Step 2 runs via the learnings store
# ---------------------------------------------------------------------------

POST_RESEARCH_TEMPLATE = """A Step 2 script just got locked at {stars} stars on the Zmarty Video
Production pipeline. We want to LEARN from this run so future scripts get better.

LOCKED SCRIPT:
{script}

QUALITY RATING reasons:
{reasons}

VALIDATOR GRADES:
{validators}

USER INPUT (what they asked for):
{user_input}

USER NOTES on advance (may be empty):
{user_notes}

GITHUB REFERENCES seen during harvest:
{github}

LOCAL SKILLS seen during harvest:
{skills}

Output VALID JSON ONLY — this gets stored in the learnings JSONL and read by FUTURE Step 2
Hermes pre-routes so it can avoid past gotchas:

{{
  "what_worked": ["<concrete patterns from THIS script that produced a high score>"],
  "what_failed": ["<concrete patterns that the fleet flagged or validators caught>"],
  "phonetic_pitfalls": ["<TTS pronunciation traps observed: 'longs' → 'lungs', 'ZmartyChat' → 'C-Marty Chat', etc>"],
  "structural_lessons": ["<what worked at the GDS-section level — hook punch, evidence transitions, CTA conversion hint>"],
  "next_video_recommendations": ["<concrete suggestion for the NEXT script the user generates>"],
  "open_source_to_evaluate": ["<repo name + why it might help>"]
}}
"""


def step2_post_research(result: dict, user_notes: str = '') -> dict:
    """After advance, distill learnings from this run for future use.
    Append to the learnings store under kind='step2_postmortem'."""
    rating = result.get('quality_rating') or {}
    validators = result.get('validators') or {}
    harvest = result.get('harvest') or {}
    payload = POST_RESEARCH_TEMPLATE.format(
        stars=rating.get('stars', '?'),
        script=(result.get('script') or '')[:2000],
        reasons='\n'.join(f'  • {r}' for r in (rating.get('reasons') or [])) or '  (none)',
        validators=json.dumps({
            'word_count': validators.get('word_count_grade'),
            'runtime': validators.get('runtime_grade'),
            'phonetic': validators.get('phonetic_grade'),
            'banned_chars': validators.get('banned_chars_grade'),
        }, indent=2),
        user_input=(result.get('user_input') or '')[:500],
        user_notes=(user_notes or '(none)')[:600],
        github=json.dumps(harvest.get('github_scripts', [])[:4], indent=2)[:800],
        skills=json.dumps(harvest.get('writing_skills', [])[:4], indent=2)[:600],
    )
    raw = _call_ollama(payload, timeout=180)
    cleaned = _strip_to_text(raw)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    spec = {}
    if m:
        try:
            spec = json.loads(m.group(0))
        except Exception:
            pass

    record = {
        'kind': 'step2_postmortem',
        'stars': rating.get('stars'),
        'user_input': result.get('user_input'),
        'topic_class': (result.get('hermes') or {}).get('topic_class'),
        'validators': {
            'word_count': validators.get('word_count'),
            'runtime_seconds': validators.get('runtime_seconds'),
            'word_count_grade': validators.get('word_count_grade'),
            'runtime_grade': validators.get('runtime_grade'),
        },
        'fleet_verdicts': rating.get('fleet_verdicts'),
        'convergence_passes': result.get('convergence_passes'),
        'polish_passes': result.get('polish_passes'),
        'what_worked': spec.get('what_worked', []),
        'what_failed': spec.get('what_failed', []),
        'phonetic_pitfalls': spec.get('phonetic_pitfalls', []),
        'structural_lessons': spec.get('structural_lessons', []),
        'next_video_recommendations': spec.get('next_video_recommendations', []),
        'open_source_to_evaluate': spec.get('open_source_to_evaluate', []),
        'user_notes': user_notes,
    }

    # Persist via learnings store
    try:
        from .learnings import record_learning
        record_learning(record)
    except Exception:
        pass
    return record


def _text_context(value, max_chars: int = 12000) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)[:max_chars]
    except Exception:
        return str(value)[:max_chars]


def _trace_step2(message: str) -> None:
    if (os.environ.get('STEP2_TRACE') or '1').strip().lower() in {'0', 'false', 'off', 'no'}:
        return
    try:
        path = PROJECT_ROOT / 'out' / 'step2_trace.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(f'[{time.strftime("%H:%M:%S")}] {message}\n')
    except Exception:
        pass


def run_step2(user_input: str, mode: str = 'fast', step1_brief: str = '',
              prior_script: str = '', notes: str = '',
              max_convergence: int = 2,
              length_seconds: int = 40,
              project: str = 'default') -> dict:
    """Run the full Step 2 script-writing pipeline.

    LENGTH-AWARE: targets (word count, runtime, GDS section count) are
    computed from length_seconds. A 15-min documentary needs ~2,000 words,
    not 90 — without this, downstream audio/subtitles/render get the wrong
    duration and the pipeline never converges.
    """
    started = time.time()
    stage_times: dict = {}
    user_input = _text_context(user_input, 4000)
    step1_brief = _text_context(step1_brief)
    prior_script = _text_context(prior_script)
    notes = _text_context(notes, 4000)

    # CRITICAL — length determines every target downstream. Compute once,
    # thread through. Falls back to current 40s/90-word default if not given.
    targets = compute_targets(length_seconds)

    # --- Stage 1: Hermes pre-route ---
    _trace_step2('start hermes_preroute')
    t = time.time()
    hermes = hermes_preroute(user_input, step1_brief=step1_brief, notes=notes)
    stage_times['hermes_preroute'] = round(time.time() - t, 1)
    _trace_step2(f'end hermes_preroute {stage_times["hermes_preroute"]}s')
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'stopped': True, 'hermes': hermes,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # Detect private topic (DansLab/Nervix/team/etc.) — flag flows to draft_script
    # so it routes through openclaude (GLM-5.1) instead of qwen2.5:7b. Also flows
    # into fleet review rubric (private = don't demand external citations).
    is_private_topic = _is_private_topic(user_input) or _is_private_topic(step1_brief)
    hermes['_is_private_topic'] = is_private_topic
    # Defensive coercion — different models return different types for these
    # fields. Coerce to string before any downstream .strip()/.lower() calls.
    for _k in ('domain', 'brand_name', 'brand_url', 'tone_target', 'fleet_owner_hint',
               'stop_reason', 'topic_class'):
        _v = hermes.get(_k)
        if _v is True or _v is False or _v is None:
            hermes[_k] = ''
        elif not isinstance(_v, str):
            hermes[_k] = str(_v)
    for _k in ('key_data_points', 'phonetic_targets', 'exclude'):
        _v = hermes.get(_k)
        if not isinstance(_v, list):
            hermes[_k] = [] if _v is None else [str(_v)]

    # --- Stage 2a: Open-source + local-tools harvest (parallel with outline) ---
    # Runs alongside the GDS outline so it's effectively free time.
    harvest: dict = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        harvest_fut = pool.submit(harvest_step2, hermes)
        # --- Stage 2b: GDS outline (length-aware) ---
        _trace_step2('start gds_prepass')
        t = time.time()
        outline = gds_prepass(user_input, hermes, step1_brief=step1_brief, targets=targets)
        stage_times['gds_outline'] = round(time.time() - t, 1)
        _trace_step2(f'end gds_prepass {stage_times["gds_outline"]}s')
        try:
            _trace_step2('start harvest_result')
            harvest = harvest_fut.result(timeout=30)
            _trace_step2('end harvest_result')
        except Exception as e:
            harvest = {'error': str(e)}
            _trace_step2(f'harvest_result error {e}')

    # --- Stage 3: draft v1 (length-aware) ---
    _trace_step2('start draft_script')
    t = time.time()
    script = draft_script(outline, hermes, notes=notes, mode=mode, targets=targets)
    stage_times['draft_v1'] = round(time.time() - t, 1)
    _trace_step2(f'end draft_script {stage_times["draft_v1"]}s')

    # --- Stage 4: polish passes (length-aware validators) ---
    polish_passes = 0
    validators = validate_script(script, targets=targets)
    while polish_passes < 2 and (
        validators['word_count_grade'] != 'GREEN' or
        validators['phonetic_grade'] != 'GREEN' or
        validators['banned_chars_grade'] != 'GREEN'
    ):
        _trace_step2(f'start polish_pass_{polish_passes + 1}')
        t = time.time()
        script = polish_pass(script, targets=targets)
        validators = validate_script(script, targets=targets)
        polish_passes += 1
        stage_times[f'polish_pass_{polish_passes}'] = round(time.time() - t, 1)
        _trace_step2(f'end polish_pass_{polish_passes} {stage_times[f"polish_pass_{polish_passes}"]}s')

    # ESCALATION — if local polish (qwen2.5:7b) couldn't land word count GREEN,
    # escalate to openclaude (GLM-5.1) for a single rewrite pass. GLM-5.1 has
    # demonstrably better instruction-following for precise word-count targets.
    # Skip if STEP2_PRIVATE_VIA=ollama (test/offline mode).
    if (validators.get('word_count_grade') != 'GREEN'
            and (os.environ.get('STEP2_PRIVATE_VIA') or 'auto').lower() != 'ollama'):
        _trace_step2('start word_count_escalation')
        t = time.time()
        # Build a topic-preserving escalation prompt — the previous version was
        # too generic and lost the topic. Now explicitly inject user_input + the
        # key data points / brand from Hermes so GLM-5.1 keeps the substance.
        kdp = ', '.join(hermes.get('key_data_points', []) or []) or '(none)'
        brand_str = hermes.get('brand_name') or '(none)'
        url_str = hermes.get('brand_url') or '(none)'
        escalation_prompt = (
            f'You rewrite a TTS-ready video narration script to land EXACTLY in '
            f'{targets["wc_target_str"]} words across {targets["n_sections"]} paragraphs '
            f'separated by blank lines.\n\n'
            f'## ORIGINAL USER REQUEST (TREAT AS GROUND TRUTH FOR TOPIC + DATA):\n'
            f'{(user_input or "")[:1200]}\n\n'
            f'## KEY DATA POINTS that MUST appear in the script:\n{kdp}\n\n'
            f'## BRAND (use exact capitalization): {brand_str}\n'
            f'## BRAND URL (spell phonetically — ".me" → "dot me"): {url_str}\n\n'
            f'## CURRENT DRAFT (improve, do NOT throw away its substance):\n{script[:4000]}\n\n'
            f'## STRICT RULES:\n'
            f'1. The rewrite MUST mention the topic from the user request and at least one key data point.\n'
            f'2. ALL numbers spelled phonetically. NO digits.\n'
            f'3. NO em-dash, en-dash, smart quotes, ellipsis. Plain ASCII only.\n'
            f'4. Exactly {targets["n_sections"]} paragraphs separated by blank lines.\n'
            f'5. Word count: {targets["wc_target_str"]}. Aim for the middle.\n'
            f'6. Tone: {hermes.get("tone_target", "confident, on-topic, no hype")}\n\n'
            f'Output ONLY the rewritten script. No preamble, no headings, no JSON.'
        )
        gpt_polished = _call_openclaude(escalation_prompt, timeout=180) if is_private_topic else _call_ollama(escalation_prompt, timeout=180)
        if gpt_polished and not gpt_polished.startswith('_('):
            gpt_polished = _strip_to_text(gpt_polished)
            gpt_polished = _enforce_paragraph_breaks(gpt_polished, targets['n_sections'])
            gpt_polished = _enforce_word_count(gpt_polished, targets['words_min'], targets['words_max'])
            new_validators = validate_script(gpt_polished, targets=targets)
            # Only accept the escalation if it actually improved the word grade
            if new_validators['word_count_grade'] != 'RED':
                script = gpt_polished
                validators = new_validators
                stage_times['polish_escalation_glm5'] = round(time.time() - t, 1)
        _trace_step2(f'end word_count_escalation {round(time.time() - t, 1)}s')

    # --- Stage 5: fleet review (with auto convergence on RED) ---
    convergence_passes = 0
    fleet_reviews: list[dict] = []
    while convergence_passes <= max_convergence:
        _trace_step2(f'start fleet_review_pass_{convergence_passes + 1}')
        t = time.time()
        fleet_reviews = fleet_review(script, validators, hermes)
        stage_times[f'fleet_review_pass_{convergence_passes+1}'] = round(time.time() - t, 1)
        _trace_step2(f'end fleet_review_pass_{convergence_passes + 1} {stage_times[f"fleet_review_pass_{convergence_passes+1}"]}s')
        red = sum(1 for r in fleet_reviews
                  if re.search(r'RED-?LIGHT', r.get('review', ''), re.I))
        if red == 0 or convergence_passes >= max_convergence:
            break
        convergence_passes += 1
        # Re-draft addressing the red lights — length-aware critique block
        critique = '\n\n'.join(
            r.get('review', '') for r in fleet_reviews
            if re.search(r'RED-?LIGHT', r.get('review', ''), re.I)
        )
        rewrite_prompt = (
            f'Rewrite this {targets["wc_target_str"]}-word script ({targets["n_sections"]} sections, '
            f'{targets["rt_target_str"]} runtime) to address the RED-LIGHT critiques below. '
            f'Keep the GDS structure, keep word count {targets["wc_target_str"]}, keep numbers phonetic.\n\n'
            f'CRITIQUES:\n{critique[:2000]}\n\nCURRENT SCRIPT:\n{script[:3000]}\n\n'
            f'Output ONLY the revised script.'
        )
        # Rewrite timeout scales with target length (long videos = longer rewrites)
        rewrite_timeout = max(180, int(targets['length_seconds'] * 0.4) + 60)
        _trace_step2(f'start rewrite_pass_{convergence_passes}')
        t = time.time()
        script = _strip_to_text(_call_ollama(rewrite_prompt, timeout=rewrite_timeout))
        validators = validate_script(script, targets=targets)
        stage_times[f'rewrite_pass_{convergence_passes}'] = round(time.time() - t, 1)
        _trace_step2(f'end rewrite_pass_{convergence_passes} {stage_times[f"rewrite_pass_{convergence_passes}"]}s')

    rating = compute_quality(script, validators, fleet_reviews, convergence_passes)
    final = consolidate_with_review(script, fleet_reviews)

    # --- Stage 6: Production narrative doc — voice direction, pacing map,
    # alternative hooks, production notes, localization, TTS tweaks, sign-off
    t = time.time()
    _trace_step2('start narrative_doc')
    narrative_doc = produce_narrative_doc(script, outline, hermes, user_input)
    stage_times['narrative_doc'] = round(time.time() - t, 1)
    _trace_step2(f'end narrative_doc {stage_times["narrative_doc"]}s')

    try:
        from .scoring import lock_step_from_run
        _reds = sum(1 for r in fleet_reviews if 'RED' in (r.get('review') or '').upper())
        lock_step_from_run(
            project=project, step=2,
            fleet={'summary': {'reds': _reds, 'greens': 0, 'yellows': 0}},
            stars=rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=rating.get('label', ''),
        )
    except Exception:
        pass

    # Spec: each locked step registers a skill (auto-cached for future runs).
    try:
        from .skill_db import register_skill
        _prompt = (user_input or '')[:500]
        _summary = f"step2 script · {(rating.get('label') or '')[:80]}"
        _excerpt = {
            'word_count': validators.get('word_count', 0),
            'runtime_seconds': validators.get('runtime_seconds', 0),
            'word_count_grade': validators.get('word_count_grade'),
            'fleet_red_count': sum(1 for r in fleet_reviews if 'RED' in (r.get('review') or '').upper()),
        }
        register_skill(
            step=2, prompt=_prompt,
            stars=rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=2, prompt=_prompt, summary=_summary,
                          result_excerpt=_excerpt, stars=rating.get('stars', 0.0))
    except Exception:
        pass

    return {
        'user_input': user_input,
        'step1_brief_used': bool(step1_brief),
        'hermes': hermes,
        'gds_outline': outline,
        'harvest': harvest,
        'script': script,
        'script_with_review': final,
        'narrative_doc': narrative_doc,
        'validators': validators,
        'fleet_reviews': fleet_reviews,
        'convergence_passes': convergence_passes,
        'polish_passes': polish_passes,
        'quality_rating': rating,
        'mode': mode,
        'iteration': bool(prior_script or notes),
        'stage_times': stage_times,
        'elapsed_seconds': round(time.time() - started, 1),
    }


# ---------------------------------------------------------------------------
# Advice generator (mirrors Step 1)
# ---------------------------------------------------------------------------

ADVICE_TEMPLATE = """You are a script-writing coach. The user produced this Step 2 narration script
on the Zmarty Video Production pipeline. It scored {stars}/5 — below the 5★ threshold.

You write FOCUSED REFINEMENT NOTES that, when fed back into the iteration loop, will
trigger a re-draft addressing the weaknesses.

USER ASK:
{user_input}

CURRENT QUALITY:
- Stars: {stars}
- Reasons:
{reasons}
- Validator grades: word={word_grade}, runtime={runtime_grade}, phonetic={phonetic_grade}
- Word count: {wc} (target {wc_target}). Runtime: {rt}s (target {rt_target}).

FLEET REVIEW:
{reviews}

CURRENT SCRIPT:
{script}

Output JSON ONLY:
{{
  "diagnosis": "<1-2 sentence diagnosis>",
  "focused_notes": "<3-5 sentence note that addresses every red-light and big yellow flag>",
  "specific_fixes": ["<concrete sentence-level fixes>"],
  "expected_lift": "<+0.5 to +1.5 stars>"
}}
"""


def step2_advise(result: dict) -> dict:
    rating = result.get('quality_rating', {}) or {}
    validators = result.get('validators', {}) or {}
    reviews = result.get('fleet_reviews', []) or []
    reviews_block = ''
    for r in reviews[:4]:
        reviews_block += f'\n--- {r.get("agent","?").upper()} ---\n{r.get("review","")[:800]}\n'
    payload = ADVICE_TEMPLATE.format(
        stars=rating.get('stars', '?'),
        reasons='\n'.join(f'  • {r}' for r in (rating.get('reasons') or [])) or '  (none)',
        user_input=(result.get('user_input') or '')[:600],
        word_grade=validators.get('word_count_grade', '?'),
        runtime_grade=validators.get('runtime_grade', '?'),
        phonetic_grade=validators.get('phonetic_grade', '?'),
        wc=validators.get('word_count', 0),
        wc_target=validators.get('word_count_target', '88-95'),
        rt=validators.get('runtime_seconds', 0),
        rt_target=validators.get('runtime_target', '38-43s'),
        reviews=reviews_block[:4000],
        script=(result.get('script') or '')[:2500],
    )
    raw = _call_ollama(payload, timeout=120)
    cleaned = _strip_to_text(raw)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            spec = json.loads(m.group(0))
            spec.setdefault('focused_notes', '')
            spec.setdefault('specific_fixes', [])
            spec.setdefault('diagnosis', '')
            spec.setdefault('expected_lift', '')
            return spec
        except Exception:
            pass
    return {
        'diagnosis': 'Auto-fallback advice (model returned non-JSON).',
        'focused_notes': '\n'.join(f'Address: {r}' for r in (rating.get('reasons') or [])),
        'specific_fixes': [],
        'expected_lift': '+0.5 stars',
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: step2_script.py "user input / data points"')
        sys.exit(1)
    result = run_step2(' '.join(sys.argv[1:]), mode='fast')
    print(json.dumps({k: v for k, v in result.items() if k != 'script_with_review'}, indent=2))
