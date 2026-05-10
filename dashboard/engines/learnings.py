#!/usr/bin/env python3.13
"""Learnings store — append-only JSONL at ~/.openclaw/learnings/zmarty_video_research.jsonl.

Every time the user advances past Step 1 (or completes a video and provides
post-mortem feedback), a record is appended. The Hermes pre-route reads the
N most recent records and injects pattern hints into its decision so the
research agent gets smarter as we produce more videos.

Record schema:
{
  "ts": ISO-8601 timestamp,
  "kind": "step1_advance" | "video_postmortem" | "user_feedback",
  "prompt": original user prompt,
  "refined_prompt": post-Hermes/GSD refined prompt,
  "intent_class": GSD-derived intent,
  "quality_rating": {stars, label, reasons},
  "fleet_verdicts": {GREEN, YELLOW, RED counts},
  "convergence_passes": int,
  "advanced_to_step2_at_stars": float (was the user satisfied?),
  "user_notes": free-form,
  "what_worked": list[str],
  "what_failed": list[str],
}
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

LEARNINGS_DIR = Path.home() / '.openclaw' / 'learnings'
LEARNINGS_FILE = LEARNINGS_DIR / 'zmarty_video_research.jsonl'

SKILLS_ROOT = Path.home() / '.claude' / 'skills'
MIN_SKILL_MD_STARS = 5.0


def _ensure_dir() -> None:
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)


def record_learning(record: dict | None = None, **fields) -> dict:
    """Append a learning record. Returns the record with `ts` filled in.

    Accepts either a single dict (`record_learning({"kind": "...", ...})`) or
    keyword arguments (`record_learning(kind="...", what_worked=[...], ...)`).
    Both forms merge into one record; kwargs override dict keys on conflict.
    """
    _ensure_dir()
    merged: dict = {}
    if isinstance(record, dict):
        merged.update(record)
    merged.update(fields)
    merged.setdefault('ts', datetime.now(timezone.utc).isoformat())
    try:
        with LEARNINGS_FILE.open('a', encoding='utf-8') as f:
            f.write(json.dumps(merged, ensure_ascii=False) + '\n')
        return {'ok': True, 'record': merged, 'path': str(LEARNINGS_FILE)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def recent_learnings(limit: int = 10) -> list[dict]:
    """Return the most recent N records (LIFO order)."""
    if not LEARNINGS_FILE.exists():
        return []
    try:
        lines = LEARNINGS_FILE.read_text(encoding='utf-8', errors='replace').splitlines()
        out: list[dict] = []
        # Walk backwards
        for line in reversed(lines[-limit*3:]):  # over-read to cap
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
                if len(out) >= limit:
                    break
            except Exception:
                continue
        return out
    except Exception:
        return []


def learnings_for_hermes(limit: int = 8) -> str:
    """Format recent learnings as a compact text block the Hermes pre-route
    can inject into its prompt so it sees what's worked/failed before."""
    recent = recent_learnings(limit=limit)
    if not recent:
        return '(no prior learnings — this is the first run)'
    lines = []
    for r in recent:
        ts = r.get('ts', '')[:10]
        prompt = (r.get('prompt') or '')[:120]
        rating = r.get('quality_rating') or {}
        stars = rating.get('stars', '?')
        verdicts = r.get('fleet_verdicts') or {}
        worked = '; '.join(r.get('what_worked', []))[:200]
        failed = '; '.join(r.get('what_failed', []))[:200]
        notes = (r.get('user_notes') or '')[:160]
        lines.append(
            f'- [{ts}] "{prompt}" → {stars}★ '
            f'(verdicts G:{verdicts.get("GREEN",0)} Y:{verdicts.get("YELLOW",0)} R:{verdicts.get("RED",0)}) '
            f'{("[notes: " + notes + "]") if notes else ""}'
            f'{(" [worked: " + worked + "]") if worked else ""}'
            f'{(" [failed: " + failed + "]") if failed else ""}'
        )
    return '\n'.join(lines)


def _yaml_escape(s: str) -> str:
    """Quote a string for YAML frontmatter — handles quotes and newlines."""
    s = (s or '').replace('\r', ' ').replace('\n', ' ').strip()
    s = s.replace('"', "'")
    return s[:280]


def _slug(text: str, max_len: int = 60) -> str:
    text = re.sub(r'[^a-zA-Z0-9]+', '-', (text or '').lower()).strip('-')
    return (text or 'pattern')[:max_len]


def generate_skill_md(step_num: int, prompt: str, summary: str = '',
                       result_excerpt: str | dict | None = None,
                       stars: float | None = None) -> dict:
    """Write a Claude skill file at ~/.claude/skills/zmarty_step{N}/SKILL.md.

    Per spec ("create skill-stepN and add to Skill database"). Idempotent —
    body uses a stable hash of the prompt so re-registering the same prompt
    updates rather than duplicates. Each step has one SKILL.md file that the
    most recent successful run rewrites; older patterns remain in skill_db
    JSONL and learnings JSONL.

    Returns {ok, path} on success or {ok: False, error} on failure.
    Wrap calls in try/except — failure must never block the pipeline.
    """
    try:
        N = int(step_num)
    except Exception:
        return {'ok': False, 'error': f'invalid step_num: {step_num}'}
    if not (1 <= N <= 10):
        return {'ok': False, 'error': f'step_num out of range: {N}'}
    if isinstance(stars, (int, float)) and float(stars) < MIN_SKILL_MD_STARS:
        return {
            'ok': False,
            'skipped': True,
            'reason': f'stars {stars} < min {MIN_SKILL_MD_STARS}',
            'step': N,
        }

    skill_dir = SKILLS_ROOT / f'zmarty_step{N}'
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {'ok': False, 'error': f'mkdir: {e}'}

    prompt_clean = (prompt or '').strip()
    prompt_hash  = hashlib.sha1(prompt_clean.encode('utf-8')).hexdigest()[:12]
    summary_clean = (summary or f'Cached pattern for Zmarty pipeline step {N}').strip()

    # Render result excerpt — accept dict or string
    if isinstance(result_excerpt, dict):
        try:
            excerpt_text = json.dumps(result_excerpt, ensure_ascii=False, indent=2)[:1800]
        except Exception:
            excerpt_text = str(result_excerpt)[:1800]
    elif isinstance(result_excerpt, str):
        excerpt_text = result_excerpt[:1800]
    else:
        excerpt_text = ''

    star_line = f'★ {stars:.1f}' if isinstance(stars, (int, float)) else ''

    description = f'Zmarty pipeline step {N} cached pattern: {summary_clean}'
    when_to_use = (
        f'When running Zmarty pipeline step {N} with a prompt similar to '
        f'"{prompt_clean[:120]}". Skip the full pipeline and reuse the cached '
        f'result excerpt below.'
    )

    body = f"""---
name: zmarty_step{N}
description: {_yaml_escape(description)}
when_to_use: {_yaml_escape(when_to_use)}
---

# Zmarty Step {N} — Cached Pattern

**Prompt hash:** `{prompt_hash}`  {star_line}

## Prompt pattern

```
{prompt_clean[:1500] or '(empty prompt)'}
```

## Summary

{summary_clean}

## Cached result excerpt

```
{excerpt_text or '(no excerpt recorded)'}
```

---
*Auto-generated by Zmarty pipeline learnings.generate_skill_md(). Re-running the same prompt updates this file in place.*
"""
    out_path = skill_dir / 'SKILL.md'
    try:
        out_path.write_text(body, encoding='utf-8')
    except Exception as e:
        return {'ok': False, 'error': f'write: {e}'}
    return {'ok': True, 'path': str(out_path), 'step': N, 'hash': prompt_hash}


def regenerate_skill_md_from_jsonl(skill_db_path: Path | str | None = None) -> dict:
    """Rebuild SKILL.md files for every step from the skill_db JSONL.

    One-shot backfill called by `/api/skills/regenerate_md`. Picks the highest-
    starred entry per step and writes it as that step's SKILL.md. Returns
    {ok, count, files: [...]}.
    """
    if skill_db_path is None:
        skill_db_path = Path.home() / '.openclaw' / 'skills' / 'skill_db.jsonl'
    p = Path(skill_db_path)
    if not p.exists():
        return {'ok': False, 'error': f'skill db not found: {p}', 'count': 0}
    try:
        lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception as e:
        return {'ok': False, 'error': f'read: {e}', 'count': 0}
    by_step: dict[int, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        step = rec.get('step')
        if not isinstance(step, int):
            continue
        cur = by_step.get(step)
        if cur is None or (rec.get('stars', 0) >= cur.get('stars', 0)):
            by_step[step] = rec
    written: list[dict] = []
    for step, rec in sorted(by_step.items()):
        res = generate_skill_md(
            step_num=step,
            prompt=rec.get('prompt_pattern') or '',
            summary=rec.get('summary') or '',
            result_excerpt=rec.get('result_excerpt'),
            stars=rec.get('stars'),
        )
        written.append({'step': step, **res})
    return {'ok': True, 'count': sum(1 for w in written if w.get('ok')),
            'files': written}


def learnings_summary() -> dict:
    """Aggregate stats across all learnings — for an evolution dashboard."""
    recent = recent_learnings(limit=999)
    if not recent:
        return {'count': 0}
    total = len(recent)
    star_sum = sum((r.get('quality_rating') or {}).get('stars', 0) for r in recent)
    verdicts = {'GREEN': 0, 'YELLOW': 0, 'RED': 0}
    convergence_total = 0
    for r in recent:
        v = r.get('fleet_verdicts') or {}
        for k in verdicts:
            verdicts[k] += v.get(k, 0)
        convergence_total += r.get('convergence_passes', 0) or 0
    return {
        'count': total,
        'avg_stars': round(star_sum / total, 2) if total else 0,
        'fleet_verdicts_total': verdicts,
        'avg_convergence_passes': round(convergence_total / total, 2) if total else 0,
        'first_run': recent[-1].get('ts', '')[:10] if recent else '',
        'last_run': recent[0].get('ts', '')[:10] if recent else '',
    }
