#!/usr/bin/env python3.13
"""Cumulative scoring store — implements the spec's "1-10 score, +1 per locked step,
8+ threshold to advance" mechanism.

Storage: per-project JSON file at ~/.openclaw/scores/{project_slug}.json

Schema:
{
  "project_slug":     "<slug>",
  "cumulative_score": 0..10,
  "predicted_score":  0..10,            # latest Hermes prediction
  "history": [
    {
      "ts":              ISO-8601,
      "step":            1..10,
      "delta":           int (typically +1),
      "predicted_score": 0..10,         # Hermes' prediction at the time
      "fleet_summary":   {greens, yellows, reds},
      "convergence_passes": int,
      "verdict":         "locked" | "rejected" | "looped",
      "notes":           "<free-form>"
    }
  ],
  "last_updated":     ISO-8601,
  "advance_threshold": 8                # min predicted_score required to advance
}

Spec mapping:
  • "Score is 1 to 10 and qualify for next step only with 8+"  → advance_threshold=8
  • "Scoring is based on a prediction that each next step will have +1 if succeed"
                                                                → predicted_score
  • "Step gets +1 score if Hermes predict a 8+ overall scoring" → record_step_lock(delta=+1)
  • "If Hermes don't get the right score → loop"               → verdict="looped"
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCORES_DIR = Path.home() / '.openclaw' / 'scores'
ADVANCE_THRESHOLD = 8
PERFECTION_STARS = float(os.environ.get('ZMARTY_PERFECTION_STARS', '5.0'))
MAX_SCORE = 10


def _slug(project: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9_-]+', '-', (project or 'default').strip().lower())
    return s.strip('-') or 'default'


def _path(project: str) -> Path:
    return SCORES_DIR / f'{_slug(project)}.json'


def _ensure_dir() -> None:
    SCORES_DIR.mkdir(parents=True, exist_ok=True)


def _empty_state(project: str) -> dict:
    return {
        'project_slug':      _slug(project),
        'cumulative_score':  0,
        'predicted_score':   0,
        'history':           [],
        'last_updated':      datetime.now(timezone.utc).isoformat(),
        'advance_threshold': ADVANCE_THRESHOLD,
        'perfection_stars':  PERFECTION_STARS,
    }


def get_score(project: str) -> dict:
    """Return current state for a project. Creates an empty record if missing."""
    p = _path(project)
    if not p.exists():
        return _empty_state(project)
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return _empty_state(project)


def reset_score(project: str) -> dict:
    """Clear state for a project. Returns the fresh empty state."""
    _ensure_dir()
    state = _empty_state(project)
    _path(project).write_text(json.dumps(state, indent=2), encoding='utf-8')
    return state


def record_step_lock(
    project: str,
    step: int,
    predicted_score: int,
    fleet_summary: dict | None = None,
    convergence_passes: int = 0,
    verdict: str = 'locked',
    delta: int = 1,
    notes: str = '',
) -> dict:
    """Record the result of a step.

    `verdict='locked'` increments cumulative_score by `delta` (typically +1).
    `verdict='looped'` or `'rejected'` records the attempt with delta=0 and
    does not increment the cumulative score — the spec's "loop until 8+"
    pattern is realised by repeated calls until verdict='locked'.

    Returns the updated state.
    """
    _ensure_dir()
    state = get_score(project)
    step = max(1, min(MAX_SCORE, int(step)))
    predicted_score = max(0, min(MAX_SCORE, int(predicted_score)))
    delta = max(0, min(1, int(delta)))
    if verdict == 'locked':
        state['cumulative_score'] = min(MAX_SCORE, state['cumulative_score'] + delta)
    else:
        delta = 0
    state['predicted_score'] = predicted_score
    state['history'].append({
        'ts':                 datetime.now(timezone.utc).isoformat(),
        'step':               step,
        'delta':              delta,
        'predicted_score':    state['predicted_score'],
        'fleet_summary':      dict(fleet_summary or {}),
        'convergence_passes': int(convergence_passes),
        'verdict':            verdict,
        'notes':              str(notes)[:500],
    })
    state['last_updated'] = datetime.now(timezone.utc).isoformat()
    _path(project).write_text(json.dumps(state, indent=2), encoding='utf-8')
    return state


def can_advance(project: str, predicted_score: int | None = None) -> dict:
    """Spec gate: returns {advance: bool, score, threshold, reason}.

    If `predicted_score` provided, evaluates that value; otherwise uses the
    last persisted prediction. The spec says: "Score is 1 to 10 and qualify
    for next step only with 8+".
    """
    state = get_score(project)
    score = predicted_score if predicted_score is not None else state.get('predicted_score', 0)
    threshold = state.get('advance_threshold', ADVANCE_THRESHOLD)
    advance = score >= threshold
    return {
        'advance':         advance,
        'predicted_score': score,
        'cumulative_score': state.get('cumulative_score', 0),
        'threshold':       threshold,
        'perfection_stars': state.get('perfection_stars', PERFECTION_STARS),
        'reason':          (
            f'Predicted score {score} >= threshold {threshold}; can advance.'
            if advance else
            f'Predicted score {score} < threshold {threshold}; loop required.'
        ),
    }


def lock_step_from_run(
    project: str,
    step: int,
    fleet: dict | None = None,
    stars: float = 0.0,
    convergence_passes: int = 0,
    notes: str = '',
) -> dict:
    """Convenience entry point for step engines.

    Maps a step's run_stepN() result to strict locked/looped semantics:
    - zero RED verdicts, all prior steps locked, and stars >= PERFECTION_STARS
      -> verdict='locked', cumulative_score += 1
    - otherwise -> verdict='looped' or 'blocked', no increment

    `stars` is a hard production gate. This keeps the dashboard from advancing
    merely because a fleet had no REDs while the step still fell short of the
    configured quality target.
    """
    fleet = fleet if isinstance(fleet, dict) else {}
    # Two shapes in this codebase:
    #   steps 8/9/10: fleet['summary'] = {greens, yellows, reds}
    #   steps 1-7:    fleet['verdicts'] = {GREEN, YELLOW, RED}
    summary = fleet.get('summary') or {}
    if not summary and fleet.get('verdicts'):
        v = fleet['verdicts']
        summary = {
            'greens':  int(v.get('GREEN',  0) or 0),
            'yellows': int(v.get('YELLOW', 0) or 0),
            'reds':    int(v.get('RED',    0) or 0),
        }
    state = get_score(project)
    locked_steps = {
        int(e.get('step', 0))
        for e in state.get('history', [])
        if e.get('verdict') == 'locked'
    }
    expected_prior = set(range(1, step))
    missing_prior = sorted(expected_prior - locked_steps)
    already_locked = step in locked_steps
    reds = int(summary.get('reds', 0) or 0)
    cumulative_now = state.get('cumulative_score', 0)
    delta = 1 if not already_locked else 0
    cumulative_after = min(MAX_SCORE, cumulative_now + delta)
    predicted_total = min(MAX_SCORE, cumulative_after + max(0, MAX_SCORE - step))
    try:
        star_value = float(stars or 0.0)
    except Exception:
        star_value = 0.0
    if reds > 0:
        verdict = 'looped'
        delta = 0
        predicted = cumulative_now
    elif missing_prior:
        verdict = 'blocked'
        delta = 0
        predicted = predicted_total
    elif star_value < PERFECTION_STARS:
        verdict = 'looped'
        delta = 0
        predicted = cumulative_now
    elif predicted_total < ADVANCE_THRESHOLD:
        verdict = 'looped'
        delta = 0
        predicted = predicted_total
    else:
        verdict = 'locked'
        predicted = predicted_total
    notes_parts = [f'stars={stars}', notes]
    if missing_prior:
        notes_parts.append(f'missing_prior_steps={missing_prior}')
    if star_value < PERFECTION_STARS:
        notes_parts.append(f'quality_below_perfection={star_value}<{PERFECTION_STARS}')
    if already_locked:
        notes_parts.append('duplicate_lock_no_delta')
    return record_step_lock(
        project=project,
        step=step,
        predicted_score=predicted,
        fleet_summary=dict(summary),
        convergence_passes=convergence_passes,
        verdict=verdict,
        delta=delta,
        notes=' | '.join(p for p in notes_parts if p)[:500],
    )


def history_summary(project: str) -> dict:
    """Compact summary suitable for the dashboard."""
    state = get_score(project)
    h = state.get('history', [])
    by_step: dict[int, dict] = {}
    for entry in h:
        s = entry['step']
        by_step.setdefault(s, {'attempts': 0, 'locks': 0, 'last_verdict': ''})
        by_step[s]['attempts'] += 1
        if entry['verdict'] == 'locked':
            by_step[s]['locks'] += 1
        by_step[s]['last_verdict'] = entry['verdict']
    return {
        'project_slug':     state.get('project_slug'),
        'cumulative_score': state.get('cumulative_score', 0),
        'predicted_score':  state.get('predicted_score', 0),
        'threshold':        state.get('advance_threshold', ADVANCE_THRESHOLD),
        'total_attempts':   len(h),
        'by_step':          by_step,
        'last_updated':     state.get('last_updated'),
    }
