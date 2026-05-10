#!/usr/bin/env python3.13
"""skills.sh client — semantic skill discovery for the pipeline.

skills.sh is "The Agent Skills Directory" — a public catalog of Claude Code /
opencode / generic-agent skills with semantic search.

Usage in the pipeline:
  - Step 1 / Step 3 can query "find skills relevant to <topic>" and surface
    high-install matches as candidates the user (or the discovery scheduler)
    can install via `claude mcp add` or by copying the SKILL.md.
  - The auto-discovery scheduler can re-query daily for newly-trending skills.

API:
  GET https://skills.sh/api/search?q=<query>&limit=<n>
  -> {query, searchType, skills:[{id, skillId, name, installs, source}], count, duration_ms}
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

BASE_URL = 'https://skills.sh'


def search(query: str, limit: int = 10, timeout: int = 15) -> dict:
    """Semantic search for skills matching `query`. Returns the raw API response.

    Falls back to {'skills': [], 'error': '...'} on network/parse failure so the
    pipeline never breaks.
    """
    if not (query or '').strip():
        return {'skills': [], 'count': 0, 'error': 'empty query'}
    qs = urllib.parse.urlencode({'q': query[:300], 'limit': max(1, min(limit, 50))})
    url = f'{BASE_URL}/api/search?{qs}'
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'skills': [], 'count': 0, 'error': f'{type(e).__name__}: {e}',
                'elapsed': round(time.time() - t0, 2)}
    data['elapsed'] = round(time.time() - t0, 2)
    return data


def top_matches(query: str, limit: int = 5, min_installs: int = 50) -> list[dict]:
    """Convenience: returns top-N skills with name, installs, source, install URL.

    Filters out toy/spam skills with fewer than `min_installs` installs.
    """
    resp = search(query, limit=limit * 3)
    skills = resp.get('skills') or []
    out = []
    for s in skills:
        if int(s.get('installs') or 0) < min_installs:
            continue
        out.append({
            'name':     s.get('name') or s.get('skillId'),
            'source':   s.get('source'),
            'installs': s.get('installs'),
            'url':      f'{BASE_URL}/{s.get("id")}',
        })
        if len(out) >= limit:
            break
    return out


def status() -> dict:
    """For dashboard provider bar — quick health ping."""
    t0 = time.time()
    try:
        req = urllib.request.Request(f'{BASE_URL}/sitemap.xml')
        with urllib.request.urlopen(req, timeout=5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    return {'available': ok, 'base_url': BASE_URL, 'latency_s': round(time.time() - t0, 2)}
