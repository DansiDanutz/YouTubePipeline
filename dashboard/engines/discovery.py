#!/usr/bin/env python3.13
"""Daily OSS discovery engine — searches for new open-source tools and models
across video, audio, rendering, quality, and design domains.

Writes ~/.openclaw/oss_registry.json which all step engines read during
their harvest phase to inject freshly-discovered tools into their prompts.

Run once daily (via cron or /api/discovery/run). Each run fetches:
  - HuggingFace trending models (video-gen, TTS, image-to-video, audio)
  - GitHub repos (video-generation, remotion, manim, stable-diffusion, etc.)
  - arXiv recent papers (video AI, TTS, audio synthesis)
  - Local tool inventory (confirms which tools are installed)

Usage:
  python3.13 -m engines.discovery          # run discovery and update registry
  from engines.discovery import run_discovery, registry_for_steps
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path.home() / '.openclaw' / 'oss_registry.json'
MAX_PER_SOURCE = 12

# Pipeline step associations per category
CATEGORY_STEPS: dict[str, list[str]] = {
    'video-gen':   ['step4_scenes', 'step7_render'],
    'audio-tts':   ['step2_script', 'step5_audio'],
    'rendering':   ['step7_render', 'step10_addons'],
    'quality':     ['step8_qa'],
    'editing':     ['step6_subtitles', 'step9_final', 'step10_addons'],
    'design':      ['step3_visual', 'step10_addons'],
}

# HuggingFace model filter tags → category label
HF_FILTERS: dict[str, str] = {
    'video-generation':  'video-gen',
    'text-to-video':     'video-gen',
    'image-to-video':    'video-gen',
    'text-to-speech':    'audio-tts',
    'audio-to-audio':    'audio-tts',
    'image-to-image':    'quality',
}

# GitHub search queries → category label
GH_QUERIES: list[tuple[str, str]] = [
    ('video generation python',      'video-gen'),
    ('text to video open source',    'video-gen'),
    ('stable diffusion video',       'video-gen'),
    ('remotion react video',         'rendering'),
    ('manim animation python',       'rendering'),
    ('ffmpeg video pipeline',        'rendering'),
    ('piper tts neural',             'audio-tts'),
    ('kokoro tts inference',         'audio-tts'),
    ('whisper transcription',        'audio-tts'),
    ('real esrgan upscaler',         'quality'),
    ('video super resolution',       'quality'),
    ('subtitle generator python',    'editing'),
    ('video editing automation',     'editing'),
    ('design system generator',      'design'),
    ('color palette generator ai',   'design'),
]

# arXiv search terms → category label
ARXIV_QUERIES: list[tuple[str, str]] = [
    ('ti:video+generation+diffusion',  'video-gen'),
    ('ti:text+to+video+synthesis',     'video-gen'),
    ('ti:neural+text+speech',          'audio-tts'),
    ('ti:video+super+resolution',      'quality'),
]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = 15) -> str:
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'btc-zmarty-discovery/1.0 (open-source research bot)',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return f'ERROR:{e}'


def _fetch_hf_category(filter_tag: str, category: str) -> list[dict]:
    url = (
        f'https://huggingface.co/api/models'
        f'?sort=trending&direction=-1&limit={MAX_PER_SOURCE}'
        f'&filter={filter_tag}'
    )
    raw = _fetch_url(url)
    if raw.startswith('ERROR:'):
        return []
    try:
        models = json.loads(raw)
    except Exception:
        return []
    out = []
    for m in models[:MAX_PER_SOURCE]:
        mid = m.get('modelId') or m.get('id', '')
        if not mid:
            continue
        out.append({
            'name': mid.split('/')[-1],
            'full_name': mid,
            'category': category,
            'source': 'huggingface',
            'url': f'https://huggingface.co/{mid}',
            'description': (m.get('cardData') or {}).get('language', filter_tag) or filter_tag,
            'stars': m.get('likes', 0),
            'downloads': m.get('downloads', 0),
            'pipeline_steps': CATEGORY_STEPS.get(category, []),
        })
    return out


def _fetch_github_repos(query: str, category: str) -> list[dict]:
    try:
        result = subprocess.run(
            ['gh', 'search', 'repos', query,
             '--sort', 'stars', '--limit', str(MAX_PER_SOURCE),
             '--json', 'nameWithOwner,description,stargazerCount,url,updatedAt'],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return []
        repos = json.loads(result.stdout or '[]')
    except Exception:
        return []
    out = []
    for r in repos:
        name = r.get('nameWithOwner', '')
        if not name:
            continue
        out.append({
            'name': name.split('/')[-1],
            'full_name': name,
            'category': category,
            'source': 'github',
            'url': r.get('url', f'https://github.com/{name}'),
            'description': (r.get('description') or '')[:200],
            'stars': r.get('stargazerCount', 0),
            'updated_at': r.get('updatedAt', ''),
            'pipeline_steps': CATEGORY_STEPS.get(category, []),
        })
    return out


def _fetch_arxiv(query: str, category: str) -> list[dict]:
    url = (
        f'https://export.arxiv.org/api/query'
        f'?search_query={query}'
        f'&sortBy=submittedDate&sortOrder=descending&max_results=5'
    )
    raw = _fetch_url(url, timeout=20)
    if raw.startswith('ERROR:'):
        return []
    entries = re.findall(
        r'<entry>([\s\S]*?)</entry>', raw, re.IGNORECASE,
    )
    out = []
    for entry in entries[:5]:
        title_m = re.search(r'<title>([\s\S]*?)</title>', entry)
        url_m = re.search(r'<id>([\s\S]*?)</id>', entry)
        summary_m = re.search(r'<summary>([\s\S]*?)</summary>', entry)
        if not title_m:
            continue
        title = re.sub(r'\s+', ' ', title_m.group(1)).strip()
        paper_url = (url_m.group(1) if url_m else '').strip()
        summary = re.sub(r'\s+', ' ', summary_m.group(1) if summary_m else '').strip()[:300]
        out.append({
            'name': title[:80],
            'full_name': title,
            'category': category,
            'source': 'arxiv',
            'url': paper_url,
            'description': summary,
            'stars': 0,
            'pipeline_steps': CATEGORY_STEPS.get(category, []),
        })
    return out


def _inventory_local_tools() -> list[dict]:
    """Check which production tools are actually installed."""
    tools = [
        ('ffmpeg',       'rendering', 'Video mux/encode/filter'),
        ('piper',        'audio-tts', 'Fast neural TTS (ONNX)'),
        ('whisper',      'audio-tts', 'OpenAI Whisper transcription'),
        ('yt-dlp',       'editing',   'YouTube/video downloader'),
        ('manim',        'rendering', 'Mathematical animation engine'),
        ('inkscape',     'design',    'Vector graphics editor'),
        ('imagemagick',  'quality',   'Image conversion & manipulation'),
        ('magick',       'quality',   'ImageMagick v7 unified binary'),
        ('blender',      'rendering', '3D rendering & compositing'),
        ('sox',          'audio-tts', 'Audio processing toolkit'),
        ('espeak-ng',    'audio-tts', 'eSpeak-NG TTS fallback'),
    ]
    out = []
    for bin_name, category, desc in tools:
        path = shutil.which(bin_name) or ''
        if path:
            out.append({
                'name': bin_name,
                'full_name': bin_name,
                'category': category,
                'source': 'local',
                'url': path,
                'description': desc,
                'stars': 0,
                'installed': True,
                'pipeline_steps': CATEGORY_STEPS.get(category, []),
            })
    return out


# ---------------------------------------------------------------------------
# Core discovery run
# ---------------------------------------------------------------------------

def run_discovery(verbose: bool = False) -> dict:
    """Fetch all sources in parallel and write the registry. Returns summary."""
    started = time.time()
    now = datetime.now(timezone.utc).isoformat()

    futures_map: dict = {}
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        # HuggingFace
        for tag, category in HF_FILTERS.items():
            f = pool.submit(_fetch_hf_category, tag, category)
            futures_map[f] = f'hf:{tag}'

        # GitHub
        for query, category in GH_QUERIES:
            f = pool.submit(_fetch_github_repos, query, category)
            futures_map[f] = f'gh:{query[:30]}'

        # arXiv
        for query, category in ARXIV_QUERIES:
            f = pool.submit(_fetch_arxiv, query, category)
            futures_map[f] = f'arxiv:{query[:30]}'

        # Local inventory (fast)
        f = pool.submit(_inventory_local_tools)
        futures_map[f] = 'local'

        for fut in as_completed(futures_map, timeout=60):
            src = futures_map[fut]
            try:
                batch = fut.result()
                if verbose:
                    print(f'  {src}: {len(batch)} items')
                results.extend(batch)
            except Exception as e:
                if verbose:
                    print(f'  {src}: ERROR {e}')

    # Deduplicate by (name, category) keeping highest stars
    seen: dict[str, dict] = {}
    for tool in results:
        key = f"{tool['category']}:{tool['name'].lower()}"
        if key not in seen or tool.get('stars', 0) > seen[key].get('stars', 0):
            seen[key] = tool

    deduped = sorted(seen.values(), key=lambda x: x.get('stars', 0), reverse=True)

    # Load existing registry to preserve previous entries not returned this run
    existing: dict = {}
    if REGISTRY_PATH.exists():
        try:
            prev = json.loads(REGISTRY_PATH.read_text('utf-8'))
            for t in prev.get('tools', []):
                k = f"{t['category']}:{t['name'].lower()}"
                existing[k] = t
        except Exception:
            pass

    # Merge: new findings win, old ones kept if not superseded
    merged: dict[str, dict] = dict(existing)
    new_count = 0
    for tool in deduped:
        key = f"{tool['category']}:{tool['name'].lower()}"
        if key not in merged:
            tool['discovered_at'] = now
            new_count += 1
        else:
            tool['discovered_at'] = merged[key].get('discovered_at', now)
        merged[key] = tool

    final_tools = sorted(merged.values(), key=lambda x: x.get('stars', 0), reverse=True)

    registry = {
        'updated_at': now,
        'tool_count': len(final_tools),
        'new_this_run': new_count,
        'categories': {
            cat: len([t for t in final_tools if t['category'] == cat])
            for cat in CATEGORY_STEPS
        },
        'tools': final_tools,
    }

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), 'utf-8')

    elapsed = round(time.time() - started, 1)
    return {
        'ok': True,
        'elapsed_seconds': elapsed,
        'tool_count': len(final_tools),
        'new_this_run': new_count,
        'categories': registry['categories'],
        'registry_path': str(REGISTRY_PATH),
        'updated_at': now,
    }


# ---------------------------------------------------------------------------
# Accessor for step engines
# ---------------------------------------------------------------------------

def registry_for_steps(steps: list[str] | None = None,
                        categories: list[str] | None = None,
                        max_tools: int = 20) -> str:
    """Return a compact text summary of registry tools relevant to given steps
    or categories. Injected into harvest prompts so engines know what's available."""
    if not REGISTRY_PATH.exists():
        return '(OSS registry not yet built — run /api/discovery/run to populate)'
    try:
        data = json.loads(REGISTRY_PATH.read_text('utf-8'))
    except Exception:
        return '(OSS registry unreadable)'

    updated = data.get('updated_at', '')[:10]
    all_tools: list[dict] = data.get('tools', [])

    if steps:
        relevant = [
            t for t in all_tools
            if any(s in t.get('pipeline_steps', []) for s in steps)
        ]
    elif categories:
        relevant = [t for t in all_tools if t.get('category') in categories]
    else:
        relevant = all_tools

    relevant = relevant[:max_tools]

    if not relevant:
        return f'(no relevant tools in OSS registry as of {updated})'

    lines = [f'OSS REGISTRY (last updated {updated}):']
    for t in relevant:
        stars = t.get('stars', 0)
        src = t.get('source', '?')
        cat = t.get('category', '?')
        installed = ' ✓installed' if t.get('installed') else ''
        desc = (t.get('description') or '')[:120]
        lines.append(
            f'  [{cat}/{src}]{installed} {t["name"]}'
            + (f' ★{stars}' if stars else '')
            + (f' — {desc}' if desc else '')
        )
    return '\n'.join(lines)


def registry_status() -> dict:
    """Return registry metadata without loading all tools."""
    if not REGISTRY_PATH.exists():
        return {'exists': False, 'path': str(REGISTRY_PATH)}
    try:
        data = json.loads(REGISTRY_PATH.read_text('utf-8'))
        return {
            'exists': True,
            'updated_at': data.get('updated_at', ''),
            'tool_count': data.get('tool_count', 0),
            'new_last_run': data.get('new_this_run', 0),
            'categories': data.get('categories', {}),
            'path': str(REGISTRY_PATH),
        }
    except Exception as e:
        return {'exists': True, 'error': str(e), 'path': str(REGISTRY_PATH)}


if __name__ == '__main__':
    import sys
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    print('Running OSS discovery...')
    result = run_discovery(verbose=verbose)
    print(json.dumps(result, indent=2))
