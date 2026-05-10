#!/usr/bin/env python3.13
"""Skill database — implements the spec's "check Skills DB for pattern; run if YES,
move to 1a if NO" mechanism.

A "skill" here is a cached, successful (step_N, prompt_pattern) entry that captured
the pipeline's prior choices. When a new prompt comes in, the dashboard checks this
DB first; on a hit, the step engine can shortcut to the cached choices instead of
re-running the full Hermes/GSD/fleet loop.

Storage: append-only JSONL at ~/.openclaw/skills/skill_db.jsonl
Index:   in-memory dict rebuilt at module load (cheap; thousands of entries fit easily)

Schema per entry:
{
  "id":             "sha1(prompt+step)",
  "step":           1..10,
  "prompt_pattern": "<original prompt, lowercased, whitespace-normalised>",
  "tokens":         ["sorted", "unique", "tokens"],
  "created_ts":     ISO-8601,
  "last_used_ts":   ISO-8601,
  "use_count":      int,
  "stars":          float (from quality_rating at the time of registration),
  "summary":        "<short label so the user can read what this skill does>",
  "result_excerpt": dict (small subset of step result for shortcutting decisions)
}

Matching: token-set Jaccard similarity ≥ THRESHOLD against the same step. Cheap,
deterministic, no embedding service needed. Spec doesn't require semantic match —
just "did we already solve this?".
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

SKILLS_DIR = Path.home() / '.openclaw' / 'skills'
SKILLS_FILE = SKILLS_DIR / 'skill_db.jsonl'

MATCH_THRESHOLD = 0.55      # Jaccard ≥ this counts as a hit
MIN_STARS_TO_REGISTER = 5.0 # Cache only perfection-gated production runs

_STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be',
    'to', 'of', 'in', 'on', 'for', 'with', 'as', 'by', 'at', 'this', 'that',
    'it', 'i', 'you', 'me', 'we', 'they', 'how', 'what', 'why', 'when',
    'about', 'do', 'does', 'did', 'can', 'could', 'should', 'would',
}


def _ensure_dir() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _normalise(prompt: str) -> str:
    return re.sub(r'\s+', ' ', (prompt or '').strip().lower())


def _tokens(prompt: str) -> list[str]:
    raw = re.findall(r'[a-z0-9]+', _normalise(prompt))
    return sorted({t for t in raw if t not in _STOPWORDS and len(t) > 1})


def _id_for(prompt: str, step: int) -> str:
    return hashlib.sha1(f'{step}:{_normalise(prompt)}'.encode('utf-8')).hexdigest()[:16]


def _jaccard(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _load_all() -> list[dict]:
    if not SKILLS_FILE.exists():
        return []
    out: list[dict] = []
    try:
        for line in SKILLS_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _rewrite_all(entries: list[dict]) -> None:
    _ensure_dir()
    SKILLS_FILE.write_text(
        ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in entries),
        encoding='utf-8',
    )


def find_skill(prompt: str, step: int, threshold: float = MATCH_THRESHOLD) -> dict:
    """Return the best-matching skill for (prompt, step) or {hit: False}."""
    entries = _load_all()
    if not entries:
        return {'hit': False, 'reason': 'skill_db_empty'}
    target = _tokens(prompt)
    if not target:
        return {'hit': False, 'reason': 'prompt_has_no_meaningful_tokens'}
    best = None
    best_score = 0.0
    for e in entries:
        if e.get('step') != step:
            continue
        score = _jaccard(e.get('tokens') or [], target)
        if score > best_score:
            best_score = score
            best = e
    if best is None or best_score < threshold:
        return {
            'hit':         False,
            'best_match':  best.get('summary') if best else None,
            'best_score':  round(best_score, 3),
            'threshold':   threshold,
            'reason':      'no_match_above_threshold',
        }
    # Update last_used / use_count and persist
    best['last_used_ts'] = datetime.now(timezone.utc).isoformat()
    best['use_count'] = int(best.get('use_count', 0)) + 1
    # Replace this entry in the file
    others = [e for e in entries if e.get('id') != best.get('id')]
    _rewrite_all(others + [best])
    return {
        'hit':         True,
        'score':       round(best_score, 3),
        'threshold':   threshold,
        'skill':       best,
    }


def register_skill(
    step: int,
    prompt: str,
    stars: float,
    summary: str = '',
    result_excerpt: dict | None = None,
    min_stars: float = MIN_STARS_TO_REGISTER,
) -> dict:
    """Cache a successful step run as a skill. No-op if stars < min_stars."""
    try:
        step = int(step)
    except Exception:
        return {'registered': False, 'reason': f'invalid_step {step!r}'}
    if not (1 <= step <= 10):
        return {'registered': False, 'reason': f'step {step} outside 1..10'}
    if stars < min_stars:
        return {'registered': False, 'reason': f'stars {stars} < min {min_stars}'}
    if not (prompt or '').strip():
        return {'registered': False, 'reason': 'empty_prompt'}

    _ensure_dir()
    sid = _id_for(prompt, step)
    now = datetime.now(timezone.utc).isoformat()
    entries = _load_all()

    # Update existing if same id, else append
    existing = next((e for e in entries if e.get('id') == sid), None)
    if existing:
        existing['last_used_ts'] = now
        existing['use_count'] = int(existing.get('use_count', 0)) + 1
        existing['stars'] = max(existing.get('stars', 0.0), stars)
        existing['summary'] = summary or existing.get('summary', '')
        existing['result_excerpt'] = result_excerpt or existing.get('result_excerpt', {})
        others = [e for e in entries if e.get('id') != sid]
        _rewrite_all(others + [existing])
        return {'registered': True, 'updated': True, 'id': sid}

    record = {
        'id':             sid,
        'step':           int(step),
        'prompt_pattern': _normalise(prompt),
        'tokens':         _tokens(prompt),
        'created_ts':     now,
        'last_used_ts':   now,
        'use_count':      1,
        'stars':          float(stars),
        'summary':        (summary or _normalise(prompt))[:200],
        'result_excerpt': result_excerpt or {},
    }
    with SKILLS_FILE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return {'registered': True, 'updated': False, 'id': sid}


def list_skills(step: int | None = None, limit: int = 100) -> list[dict]:
    """Return skills, optionally filtered to one step. Sorted by last_used_ts desc."""
    entries = _load_all()
    if step is not None:
        entries = [e for e in entries if e.get('step') == step]
    entries.sort(key=lambda e: e.get('last_used_ts', ''), reverse=True)
    # Trim heavy fields for the dashboard
    out = []
    for e in entries[:limit]:
        out.append({
            'id':             e.get('id'),
            'step':           e.get('step'),
            'summary':        e.get('summary'),
            'prompt_pattern': e.get('prompt_pattern'),
            'stars':          e.get('stars'),
            'use_count':      e.get('use_count'),
            'last_used_ts':   e.get('last_used_ts'),
            'created_ts':     e.get('created_ts'),
        })
    return out


def delete_skill(skill_id: str) -> dict:
    entries = _load_all()
    before = len(entries)
    kept = [e for e in entries if e.get('id') != skill_id]
    if len(kept) == before:
        return {'deleted': False, 'reason': 'not_found'}
    _rewrite_all(kept)
    return {'deleted': True, 'id': skill_id}


def db_summary() -> dict:
    entries = _load_all()
    by_step: dict[int, int] = {}
    for e in entries:
        by_step[e.get('step', 0)] = by_step.get(e.get('step', 0), 0) + 1
    return {
        'total_skills':  len(entries),
        'by_step':       dict(sorted(by_step.items())),
        'path':          str(SKILLS_FILE),
        'threshold':     MATCH_THRESHOLD,
        'min_stars':     MIN_STARS_TO_REGISTER,
    }
