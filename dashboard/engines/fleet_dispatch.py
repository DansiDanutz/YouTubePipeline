#!/usr/bin/env python3.13
"""Fleet dispatch — bridge between the Step 1 research brief and David's
OpenClaw fleet (Dexter / Memo / Sienna / Nano) via Hermes-style routing.

Key surfaces this module talks to:

  1. ~/.openclaw/workspace/dispatch_task.py
        Safe dispatch gate. Runs DelegationManager checks (circuit breaker,
        memory, queue, rate limit) before XADD'ing to dls.tasks.{agent}.
        We shell out to it so we inherit ALL its safety guarantees.

  2. Hermes HCI (http://localhost:10272)
        The master orchestration dashboard. If it is reachable, we POST
        the brief to /api/intake (or whatever endpoint exists) so Hermes
        sees the new work and can re-route if its own routing policy
        prefers a different specialist.

  3. OpenClaw gateway (http://localhost:18789)
        The unified gateway — used here for /health and /agents probes
        so the dashboard can show live fleet workload before dispatching.

If Redis or Hermes/OpenClaw aren't running, every function degrades
gracefully and returns a structured 'skipped' response. The UI surfaces
those reasons so the user knows what to start.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
DISPATCH_SCRIPT = HOME / '.openclaw' / 'workspace' / 'dispatch_task.py'
HERMES_HCI = os.environ.get('HERMES_HCI_URL', 'http://localhost:10272')
OPENCLAW_GATEWAY = os.environ.get('OPENCLAW_GATEWAY_URL', 'http://localhost:18789')

VALID_AGENTS = {'dexter', 'memo', 'sienna', 'nano'}


# ---------------------------------------------------------------------------
# Probes — used for the harvest section so the brief shows live availability
# ---------------------------------------------------------------------------

def probe_hermes() -> dict:
    """Return Hermes HCI reachability + a tiny status snapshot if reachable."""
    try:
        req = urllib.request.Request(f'{HERMES_HCI}/api/health',
                                     headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=2) as r:
            body = r.read().decode('utf-8', errors='replace')
        return {'reachable': True, 'url': HERMES_HCI, 'response': body[:300]}
    except Exception as e:
        return {'reachable': False, 'url': HERMES_HCI, 'reason': str(e)[:140]}


def probe_openclaw_gateway() -> dict:
    try:
        req = urllib.request.Request(f'{OPENCLAW_GATEWAY}/health')
        with urllib.request.urlopen(req, timeout=2) as r:
            body = r.read().decode('utf-8', errors='replace')
        return {'reachable': True, 'url': OPENCLAW_GATEWAY, 'response': body[:300]}
    except Exception as e:
        return {'reachable': False, 'url': OPENCLAW_GATEWAY, 'reason': str(e)[:140]}


def fleet_workload() -> dict:
    """Run dispatch_task.py --status to get per-agent health (memory, circuit
    breaker, queue depth, rate limit). The output drives the synthesizer's
    'who is free to take this' recommendation."""
    if not DISPATCH_SCRIPT.exists():
        return {'available': False, 'reason': f'{DISPATCH_SCRIPT} not found'}
    try:
        out = subprocess.run(
            ['python3', str(DISPATCH_SCRIPT), '--status'],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0:
            return {'available': False, 'reason': out.stderr.strip()[:300]}
        return {'available': True, 'raw_status': out.stdout[:1500]}
    except Exception as e:
        return {'available': False, 'reason': str(e)[:140]}


def fleet_integration_snapshot() -> dict:
    """One-shot snapshot used by the engine harvest. Cheap (<3s)."""
    return {
        'hermes': probe_hermes(),
        'openclaw_gateway': probe_openclaw_gateway(),
        'workload': fleet_workload(),
        'dispatch_script_present': DISPATCH_SCRIPT.exists(),
    }


# ---------------------------------------------------------------------------
# Actions — actually push work to the fleet
# ---------------------------------------------------------------------------

def dispatch_to_agent(agent: str, payload: str, *, task_type: str = 'instruction',
                      priority: str = 'normal', dry_run: bool = False) -> dict:
    """Push a task to a fleet agent via the safe dispatch gate.

    Returns: {ok, agent, task_id?, exit_code, stdout, stderr, dispatched_at}
    """
    agent = (agent or '').lower().strip()
    if agent not in VALID_AGENTS:
        return {'ok': False, 'reason': f'Unknown agent: {agent}. Must be one of {sorted(VALID_AGENTS)}'}
    if not DISPATCH_SCRIPT.exists():
        return {'ok': False, 'reason': f'Dispatch script missing: {DISPATCH_SCRIPT}'}
    if not payload or not payload.strip():
        return {'ok': False, 'reason': 'Empty payload'}

    # Truncate payload to a sensible Redis stream entry size (32 KB).
    payload_clipped = payload.strip()[:32_000]
    if dry_run:
        # dispatch_task.py --check AGENT runs the gate without writing to Redis
        cmd = ['python3', str(DISPATCH_SCRIPT), '--check', agent]
    else:
        cmd = [
            'python3', str(DISPATCH_SCRIPT),
            '--agent', agent,
            '--type', task_type,
            '--payload', payload_clipped,
            '--priority', priority,
        ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        # dispatch_task.py exit codes: 0=accepted, 1=rejected, 2=error
        return {
            'ok': out.returncode == 0,
            'agent': agent,
            'mode': 'dry-run' if dry_run else 'dispatched',
            'exit_code': out.returncode,
            'stdout': out.stdout[:1200],
            'stderr': out.stderr[:600],
        }
    except subprocess.TimeoutExpired:
        return {'ok': False, 'reason': 'Dispatch timed out (Redis unreachable?)'}
    except Exception as e:
        return {'ok': False, 'reason': str(e)[:240]}


def notify_hermes(brief_excerpt: str, source_step: str = 'step1') -> dict:
    """Best-effort POST to Hermes HCI so it knows about the new brief.
    Hermes can then run its own routing policy and possibly override our
    recommended fleet owner. If Hermes isn't reachable, we degrade silently."""
    try:
        body = json.dumps({
            'source': f'zmarty-dashboard:{source_step}',
            'kind': 'research_brief',
            'excerpt': brief_excerpt[:4000],
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{HERMES_HCI}/api/intake',
            data=body, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            return {'ok': True, 'response': r.read().decode('utf-8', errors='replace')[:300]}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'ok': False, 'reason': 'Hermes /api/intake not implemented yet (HTTP 404)'}
        return {'ok': False, 'reason': f'Hermes HTTP {e.code}'}
    except Exception as e:
        return {'ok': False, 'reason': f'Hermes unreachable: {str(e)[:140]}'}
