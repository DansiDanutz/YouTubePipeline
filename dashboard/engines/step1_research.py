#!/usr/bin/env python3.13
"""Step 1 — Professional Research & Competitive Analysis engine.

A senior-engineer research tool that runs many harvesters in parallel and
produces a decision-grade Markdown brief. Inspired by the best practices
from the local `deep-research`, `market-research`, and `research-ops`
skills, plus the 2026 industry survey of top research APIs.

------------------------------------------------------------------
HARVESTERS (parallel, all rate-limited gracefully)
------------------------------------------------------------------

Free tier (no API key):
  - GitHub repos (`gh search repos`, widening fallback)
  - GitHub Trending (top-starred this week, by language)
  - HackerNews (Algolia HTTPS API)
  - Lobsters (lobste.rs RSS — engineering signal)
  - Reddit (search.json)
  - DuckDuckGo (Instant Answer API)
  - arXiv (academic papers)
  - Papers With Code (ML research with code)
  - Hugging Face Hub (models + datasets)
  - YouTube (via Piped public instance, no key)
  - Local Claude skills (~/.claude/skills + plugins)
  - Local Claude agents (~/.claude/agents)
  - Installed CLI inventory
  - Local Ollama models
  - Tailscale fleet roster

Capability-gated (auto-detected from process env or ~/.openclaw/fleet.env):
  - Brave Search           → DLS_BRAVE_API_KEY     (free 2k/mo, often exhausted)
  - Exa.ai                 → EXA_API_KEY           ($25-100/mo, neural/semantic)
  - Tavily                 → TAVILY_API_KEY        ($10/mo, agent-tuned)
  - Firecrawl              → FIRECRAWL_API_KEY     ($20/mo, structured page extract)
  - Perplexity Sonar       → PERPLEXITY_API_KEY    (search-grounded synthesis)
  - SerpAPI                → SERPAPI_API_KEY       ($75/mo, real Google SERPs)
  - Mediastack News        → MEDIASTACK_API_KEY    ($25/mo, current news)
  - You.com Research       → YOU_API_KEY           (alt deep-research)
  - Kagi Search            → KAGI_API_KEY          ($25/1k, privacy-first)
  - Apify (X/LinkedIn/YT)  → APIFY_API_TOKEN       ($5/mo, social scrape)

Synthesis modes:
  - "fast" : free harvesters + local Ollama (qwen3:8b general)   ~25-40s
  - "deep" : all available + Perplexity sonar-pro w/ citations    ~40-90s

------------------------------------------------------------------
OUTPUT (decision-grade brief)
------------------------------------------------------------------

The synthesis Markdown follows the research standards lifted from the
ECC market-research skill: fact / inference / recommendation are clearly
separated, every important claim has a source, recency is called out
when stale, contrarian evidence is included.

Sections (in order):
  1. Verdict + confidence (LOW/MEDIUM/HIGH)
  2. Sub-questions decomposed from the prompt
  3. Top 5 Open-Source Adoptions (link + why-fit + lift + avoid)
  4. Comparative Matrix (Markdown table of options)
  5. Recommended Local Stack (concrete identifiers from harvest)
  6. Fleet Owner (which specialist executes)
  7. Architectural Decisions Locked
  8. Risks, Gaps & Contradictions
  9. Recency Audit (which sources are stale)
 10. Bibliography (numbered citations)
 11. Subscription Advisor (which paid APIs would unlock more value)
 12. Next Action (one concrete command or PR description)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path

HOME = Path.home()
SKILLS_DIR = HOME / '.claude' / 'skills'
AGENTS_DIR = HOME / '.claude' / 'agents'
PLUGINS_DIR = HOME / '.claude' / 'plugins'
FLEET_ENV = HOME / '.openclaw' / 'fleet.env'

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LOCAL_SYNTHESIS_MODEL = os.environ.get('STEP1_LOCAL_MODEL', 'qwen3:8b')
DEEP_SYNTHESIS_MODEL = os.environ.get('STEP1_DEEP_MODEL', 'sonar-pro')

UA = 'ZmartyDashboardStep1Engine/2.0'


# ---------------------------------------------------------------------------
# Env / key resolution
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
# Keyword extraction
# ---------------------------------------------------------------------------

STOPWORDS = {
    'the', 'and', 'for', 'that', 'this', 'with', 'have', 'will', 'from',
    'about', 'want', 'need', 'good', 'best', 'into', 'over', 'than',
    'should', 'would', 'could', 'make', 'made', 'use', 'using', 'used',
    'when', 'where', 'what', 'which', 'how', 'why', 'who', 'are', 'was',
    'were', 'been', 'being', 'has', 'had', 'can', 'just', 'them', 'they',
    'their', 'there', 'these', 'those', 'some', 'any', 'each', 'all',
    'both', 'between', 'through', 'because', 'while', 'also', 'very',
    'really', 'much', 'most', 'more', 'less', 'few', 'one', 'two', 'three',
    'lets', 'let', 'get', 'got', 'see', 'now', 'then', 'still', 'such',
}


def extract_keywords(prompt: str, limit: int = 6) -> list[str]:
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b", prompt.lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for t in tokens:
        if t in STOPWORDS or t in seen:
            continue
        seen.add(t)
        keywords.append(t)
    keywords.sort(key=lambda w: (-len(w), w))
    return keywords[:limit]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _http(url: str, *, headers: dict | None = None, data: bytes | None = None,
          method: str = 'GET', timeout: int = 15) -> bytes:
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _http_json(url: str, *, headers: dict | None = None, data: bytes | None = None,
               method: str = 'GET', timeout: int = 15) -> dict | list:
    if headers is None:
        headers = {}
    headers.setdefault('Accept', 'application/json')
    return json.loads(_http(url, headers=headers, data=data, method=method,
                            timeout=timeout).decode('utf-8', errors='replace'))


# ---------------------------------------------------------------------------
# FREE harvesters
# ---------------------------------------------------------------------------

def harvest_github(keywords: list[str]) -> list[dict]:
    if not keywords:
        return []
    queries: list[str] = []
    if len(keywords) >= 2:
        queries.append(' '.join(keywords[:2]))
    queries.append(keywords[0])
    last_err = ''
    for query in queries:
        try:
            out = subprocess.run(
                ['gh', 'search', 'repos',
                 '--json', 'fullName,description,stargazersCount,url,updatedAt,language',
                 '--sort', 'stars', '--limit', '12', '--', query],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0 and out.stdout.strip():
                hits = json.loads(out.stdout)
                if hits:
                    return [{'_query': query, **(h if isinstance(h, dict) else {})} for h in hits]
            last_err = out.stderr.strip()[:200]
        except FileNotFoundError:
            return [{'error': 'gh CLI not installed'}]
        except subprocess.TimeoutExpired:
            return [{'error': 'gh search timed out'}]
        except Exception as e:
            last_err = str(e)
    return [{'error': last_err or 'gh search returned no results'}]


def harvest_github_trending(keywords: list[str]) -> list[dict]:
    """Top-starred repos created in the last 7 days. Always uses `gh api`
    so the call is authenticated through the user's gh credentials (higher
    rate limit, no token leakage)."""
    try:
        from datetime import datetime, timedelta, timezone
        lang = next((kw for kw in keywords if kw in {
            'python', 'javascript', 'typescript', 'rust', 'go', 'cpp',
            'kotlin', 'swift', 'ruby', 'java', 'php', 'csharp'
        }), '')
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
        q = f'created:>{since}'
        if lang:
            q += f' language:{lang}'
        out = subprocess.run(
            ['gh', 'api',
             '-H', 'Accept: application/vnd.github+json',
             '-X', 'GET',
             '/search/repositories',
             '-f', f'q={q}',
             '-f', 'sort=stars',
             '-f', 'order=desc',
             '-f', 'per_page=10'],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return [{'error': f'gh api failed: {out.stderr.strip()[:200]}'}]
        data = json.loads(out.stdout)
        items = data.get('items', []) if isinstance(data, dict) else []
        return [{
            'fullName': r.get('full_name'),
            'description': (r.get('description') or '')[:240],
            'stargazersCount': r.get('stargazers_count', 0),
            'url': r.get('html_url'),
            'language': r.get('language'),
            'created': (r.get('created_at') or '')[:10],
        } for r in items[:8]]
    except FileNotFoundError:
        return [{'error': 'gh CLI not installed'}]
    except Exception as e:
        return [{'error': f'GitHub trending failed: {e}'}]


def harvest_hackernews(keywords: list[str]) -> list[dict]:
    if not keywords:
        return []
    q = urllib.parse.quote(' '.join(keywords[:3]))
    url = f'https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage=10'
    try:
        data = _http_json(url, timeout=12)
        hits = data.get('hits', []) if isinstance(data, dict) else []
        return [{
            'title': (h.get('title') or h.get('story_title') or '')[:180],
            'url': h.get('url') or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            'points': h.get('points', 0),
            'comments': h.get('num_comments', 0),
            'author': h.get('author', ''),
            'created': (h.get('created_at') or '')[:10],
        } for h in hits[:8]]
    except Exception as e:
        return [{'error': f'HackerNews failed: {e}'}]


def harvest_lobsters(keywords: list[str]) -> list[dict]:
    """lobste.rs hottest stories — high signal-to-noise engineering community."""
    if not keywords:
        return []
    # Lobsters has a /search RSS endpoint
    q = urllib.parse.quote(' '.join(keywords[:2]))
    url = f'https://lobste.rs/search?q={q}&what=stories&order=relevance&format=json'
    try:
        data = _http_json(url, timeout=10)
        items = data if isinstance(data, list) else []
        return [{
            'title': (it.get('title') or '')[:180],
            'url': it.get('url') or it.get('short_id_url') or '',
            'score': it.get('score', 0),
            'comments': it.get('comment_count', 0),
            'tags': ','.join(it.get('tags', []))[:80],
            'created': (it.get('created_at') or '')[:10],
        } for it in items[:6]]
    except Exception as e:
        return [{'error': f'Lobsters failed: {e}'}]


def harvest_reddit(keywords: list[str]) -> list[dict]:
    if not keywords:
        return []
    q = urllib.parse.quote(' '.join(keywords[:3]))
    url = f'https://www.reddit.com/search.json?q={q}&sort=top&t=year&limit=10'
    try:
        data = _http_json(url, headers={'User-Agent': UA}, timeout=12)
        posts = data.get('data', {}).get('children', []) if isinstance(data, dict) else []
        return [{
            'title': (p.get('data', {}).get('title') or '')[:180],
            'subreddit': p.get('data', {}).get('subreddit', ''),
            'url': 'https://reddit.com' + p.get('data', {}).get('permalink', ''),
            'score': p.get('data', {}).get('score', 0),
            'comments': p.get('data', {}).get('num_comments', 0),
        } for p in posts[:8]]
    except Exception as e:
        return [{'error': f'Reddit failed: {e}'}]


def harvest_duckduckgo(keywords: list[str]) -> list[dict]:
    if not keywords:
        return []
    q = urllib.parse.quote(' '.join(keywords[:3]))
    url = f'https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1'
    try:
        data = _http_json(url, timeout=10)
        if not isinstance(data, dict):
            return []
        out: list[dict] = []
        if data.get('AbstractText'):
            out.append({
                'kind': 'abstract',
                'source': data.get('AbstractSource', 'DuckDuckGo'),
                'title': data.get('Heading', ''),
                'text': data['AbstractText'][:600],
                'url': data.get('AbstractURL', ''),
            })
        for t in (data.get('RelatedTopics') or [])[:5]:
            if isinstance(t, dict) and t.get('Text'):
                out.append({
                    'kind': 'related',
                    'text': t['Text'][:300],
                    'url': t.get('FirstURL', ''),
                })
        return out
    except Exception as e:
        return [{'error': f'DuckDuckGo failed: {e}'}]


def harvest_arxiv(keywords: list[str]) -> list[dict]:
    """arXiv API — free, no key. Best for academic / research-paper signal."""
    if not keywords:
        return []
    q = '+AND+'.join(f'all:{urllib.parse.quote(k)}' for k in keywords[:3])
    url = f'http://export.arxiv.org/api/query?search_query={q}&start=0&max_results=8&sortBy=submittedDate&sortOrder=descending'
    try:
        body = _http(url, timeout=12).decode('utf-8', errors='replace')
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        root = ET.fromstring(body)
        out = []
        for entry in root.findall('a:entry', ns):
            title = (entry.findtext('a:title', '', ns) or '').strip().replace('\n', ' ')
            summary = (entry.findtext('a:summary', '', ns) or '').strip().replace('\n', ' ')
            link = entry.findtext('a:id', '', ns)
            published = entry.findtext('a:published', '', ns)
            authors = [a.findtext('a:name', '', ns) for a in entry.findall('a:author', ns)]
            out.append({
                'title': title[:200],
                'summary': summary[:400],
                'url': link,
                'published': (published or '')[:10],
                'authors': ', '.join(authors[:3]),
            })
        return out
    except Exception as e:
        return [{'error': f'arXiv failed: {e}'}]


def harvest_huggingface(keywords: list[str]) -> list[dict]:
    """Hugging Face Hub — top models + datasets matching the prompt. Free."""
    if not keywords:
        return []
    q = urllib.parse.quote(' '.join(keywords[:2]))
    out: list[dict] = []
    try:
        models = _http_json(f'https://huggingface.co/api/models?search={q}&sort=downloads&direction=-1&limit=6', timeout=12)
        for m in models if isinstance(models, list) else []:
            out.append({
                'kind': 'model',
                'id': m.get('id') or m.get('modelId'),
                'downloads': m.get('downloads', 0),
                'likes': m.get('likes', 0),
                'url': f"https://huggingface.co/{m.get('id') or m.get('modelId','')}",
            })
    except Exception as e:
        out.append({'error': f'HF models failed: {e}'})
    try:
        datasets = _http_json(f'https://huggingface.co/api/datasets?search={q}&sort=downloads&direction=-1&limit=4', timeout=12)
        for d in datasets if isinstance(datasets, list) else []:
            out.append({
                'kind': 'dataset',
                'id': d.get('id'),
                'downloads': d.get('downloads', 0),
                'likes': d.get('likes', 0),
                'url': f"https://huggingface.co/datasets/{d.get('id','')}",
            })
    except Exception as e:
        out.append({'error': f'HF datasets failed: {e}'})
    return out


def harvest_papers_with_code(keywords: list[str]) -> list[dict]:
    """Papers With Code — research with linked implementations."""
    if not keywords:
        return []
    q = urllib.parse.quote(' '.join(keywords[:2]))
    url = f'https://paperswithcode.com/api/v1/papers/?q={q}&items_per_page=5'
    try:
        data = _http_json(url, timeout=12)
        results = data.get('results', []) if isinstance(data, dict) else []
        out = []
        for p in results[:6]:
            out.append({
                'title': (p.get('title') or '')[:200],
                'abstract': (p.get('abstract') or '')[:350],
                'url': p.get('url_pdf') or p.get('url_abs') or '',
                'published': (p.get('published') or '')[:10],
            })
        return out
    except Exception as e:
        return [{'error': f'PapersWithCode failed: {e}'}]


def harvest_youtube(keywords: list[str]) -> list[dict]:
    """YouTube via Piped public API (free, no key, privacy-respecting frontend).
    Falls back silently if the public instance is rate-limited."""
    if not keywords:
        return []
    q = urllib.parse.quote(' '.join(keywords[:3]))
    instances = [
        'https://pipedapi.kavin.rocks',
        'https://pipedapi.tokhmi.xyz',
        'https://pipedapi.adminforge.de',
    ]
    for base in instances:
        try:
            data = _http_json(f'{base}/search?q={q}&filter=videos', timeout=10)
            items = data.get('items', []) if isinstance(data, dict) else []
            out = []
            for v in items[:6]:
                out.append({
                    'title': (v.get('title') or '')[:200],
                    'channel': (v.get('uploaderName') or '')[:80],
                    'views': v.get('views', 0),
                    'duration_s': v.get('duration', 0),
                    'url': 'https://youtube.com' + (v.get('url') or ''),
                    'uploaded': v.get('uploadedDate', ''),
                })
            if out:
                return out
        except Exception:
            continue
    return [{'error': 'YouTube (Piped) public instances unreachable'}]


# ---------------------------------------------------------------------------
# CAPABILITY-GATED harvesters (paid APIs)
# ---------------------------------------------------------------------------

def harvest_brave(keywords: list[str]) -> list[dict]:
    key = _key('BRAVE_API_KEY', 'DLS_BRAVE_API_KEY')
    if not key or not keywords:
        return [{'skipped': 'no Brave key'}]
    q = urllib.parse.quote(' '.join(keywords[:3]))
    try:
        data = _http_json(
            f'https://api.search.brave.com/res/v1/web/search?q={q}&count=10',
            headers={'X-Subscription-Token': key, 'Accept': 'application/json'},
            timeout=12,
        )
        results = (data.get('web') or {}).get('results', []) if isinstance(data, dict) else []
        return [{
            'title': (r.get('title') or '')[:200],
            'url': r.get('url', ''),
            'description': (r.get('description') or '')[:300],
            'age': r.get('age', ''),
        } for r in results[:8]]
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return [{'error': 'Brave quota exhausted'}]
        return [{'error': f'Brave HTTP {e.code}'}]
    except Exception as e:
        return [{'error': f'Brave failed: {e}'}]


def harvest_perplexity_search(keywords: list[str], prompt: str) -> list[dict]:
    """Perplexity Search API — official client (perplexityai 0.32+).
    Returns search results with title/url/snippet, grounded in current web.
    Used as a primary harvest source whenever the key is present."""
    key = _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY')
    if not key or not prompt:
        return [{'skipped': 'no Perplexity key'}]
    try:
        from perplexity import Perplexity  # type: ignore
        client = Perplexity(api_key=key)
        res = client.search.create(query=prompt[:300], max_results=8)
        out: list[dict] = []
        for r in (res.results or [])[:8]:
            out.append({
                'title':   (getattr(r, 'title', '') or '')[:220],
                'url':     getattr(r, 'url', '') or '',
                'snippet': (getattr(r, 'snippet', '') or '')[:400],
                'date':    (getattr(r, 'date', '') or '')[:10],
            })
        return out
    except ImportError:
        return [{'error': 'perplexityai not installed (pip install perplexityai)'}]
    except Exception as e:
        return [{'error': f'Perplexity Search failed: {e}'}]


def harvest_exa(keywords: list[str], prompt: str) -> list[dict]:
    """Exa.ai — neural / semantic web search. Best-in-class for finding
    high-quality, recent, niche content. Returns content highlights."""
    key = _key('EXA_API_KEY')
    if not key or not keywords:
        return [{'skipped': 'no Exa key — see subscription advisor'}]
    body = json.dumps({
        'query': prompt[:300],
        'type': 'auto',
        'numResults': 10,
        'contents': {'highlights': {'numSentences': 2, 'highlightsPerUrl': 2}},
    }).encode('utf-8')
    try:
        data = _http_json(
            'https://api.exa.ai/search',
            headers={'x-api-key': key, 'Content-Type': 'application/json'},
            data=body, method='POST', timeout=20,
        )
        results = data.get('results', []) if isinstance(data, dict) else []
        return [{
            'title': (r.get('title') or '')[:200],
            'url': r.get('url', ''),
            'published': (r.get('publishedDate') or '')[:10],
            'highlights': r.get('highlights', [])[:2],
            'score': r.get('score'),
            'author': r.get('author', ''),
        } for r in results[:8]]
    except Exception as e:
        return [{'error': f'Exa failed: {e}'}]


def harvest_tavily(keywords: list[str], prompt: str) -> list[dict]:
    """Tavily — agent-tuned research search with built-in summary."""
    key = _key('TAVILY_API_KEY')
    if not key or not keywords:
        return [{'skipped': 'no Tavily key — see subscription advisor'}]
    body = json.dumps({
        'api_key': key,
        'query': prompt[:300],
        'search_depth': 'advanced',
        'max_results': 10,
        'include_answer': True,
    }).encode('utf-8')
    try:
        data = _http_json(
            'https://api.tavily.com/search',
            headers={**{'Content-Type': 'application/json'}, **({'Authorization': f'Bearer {os.environ.get("OLLAMA_API_KEY","")}'} if os.environ.get('OLLAMA_API_KEY') else {})},
            data=body, method='POST', timeout=25,
        )
        results = data.get('results', []) if isinstance(data, dict) else []
        out = []
        if data.get('answer'):
            out.append({'kind': 'tavily_answer', 'text': data['answer'][:800]})
        out.extend({
            'title': (r.get('title') or '')[:200],
            'url': r.get('url', ''),
            'snippet': (r.get('content') or '')[:300],
            'score': r.get('score'),
            'published': r.get('published_date', ''),
        } for r in results[:8])
        return out
    except Exception as e:
        return [{'error': f'Tavily failed: {e}'}]


def harvest_firecrawl(top_urls: list[str]) -> list[dict]:
    """Firecrawl — clean Markdown extraction of the top URLs found by web search.
    Adds depth to the synthesis. Costs 1 credit per page (~$0.001)."""
    key = _key('FIRECRAWL_API_KEY')
    if not key or not top_urls:
        return [{'skipped': 'no Firecrawl key — see subscription advisor'}]
    out = []
    for url in top_urls[:3]:
        try:
            body = json.dumps({'url': url, 'formats': ['markdown']}).encode('utf-8')
            data = _http_json(
                'https://api.firecrawl.dev/v1/scrape',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                data=body, method='POST', timeout=30,
            )
            md = (data.get('data') or {}).get('markdown', '') if isinstance(data, dict) else ''
            out.append({'url': url, 'markdown_excerpt': md[:1500]})
        except Exception as e:
            out.append({'url': url, 'error': str(e)})
    return out


def harvest_serpapi(keywords: list[str]) -> list[dict]:
    """SerpAPI — real Google SERPs (parsed PAA, sitelinks, ads, etc.)."""
    key = _key('SERPAPI_API_KEY')
    if not key or not keywords:
        return [{'skipped': 'no SerpAPI key — see subscription advisor'}]
    q = urllib.parse.quote(' '.join(keywords[:3]))
    try:
        data = _http_json(
            f'https://serpapi.com/search.json?engine=google&q={q}&api_key={key}&num=10',
            timeout=15,
        )
        organic = data.get('organic_results', []) if isinstance(data, dict) else []
        return [{
            'title': (r.get('title') or '')[:200],
            'url': r.get('link', ''),
            'snippet': (r.get('snippet') or '')[:300],
            'position': r.get('position'),
            'date': r.get('date', ''),
        } for r in organic[:8]]
    except Exception as e:
        return [{'error': f'SerpAPI failed: {e}'}]


def harvest_brightdata(keywords: list[str], prompt: str = '') -> list[dict]:
    """Bright Data — real-browser SERP API (proxy network, ~99% uptime).

    Uses Bright Data's SERP API to fetch Google results without bot detection.
    Activates when BRIGHTDATA_API_TOKEN is set (resolved from keychain via _secrets).
    """
    key = _key('BRIGHTDATA_API_TOKEN', 'BRIGHTDATA_API_KEY')
    if not key or not keywords:
        return [{'skipped': 'no BrightData token'}]
    # SERP API endpoint — POST with query, get back parsed Google results
    q = ' '.join(keywords[:3])
    try:
        body = json.dumps({
            'zone': 'serp_api1',           # default zone name; override via env
            'url':  f'https://www.google.com/search?q={urllib.parse.quote(q)}&num=10&brd_json=1',
            'format': 'raw',
        }).encode('utf-8')
        data = _http_json(
            'https://api.brightdata.com/request',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            data=body, method='POST', timeout=20,
        )
        # Bright Data returns JSON with parsed organic results when brd_json=1
        if isinstance(data, dict):
            organic = data.get('organic') or data.get('results') or []
            out = []
            for r in organic[:8]:
                if not isinstance(r, dict):
                    continue
                out.append({
                    'title':   (r.get('title') or '')[:200],
                    'url':     r.get('link') or r.get('url', ''),
                    'snippet': (r.get('description') or r.get('snippet') or '')[:300],
                    'position': r.get('rank') or r.get('position'),
                })
            return out or [{'note': 'BrightData returned no organic results'}]
        return [{'note': 'BrightData returned non-JSON; check zone config'}]
    except Exception as e:
        return [{'error': f'BrightData failed: {e}'}]


def harvest_news(keywords: list[str]) -> list[dict]:
    """Mediastack — current news (free RSS-style fallback if no key)."""
    key = _key('MEDIASTACK_API_KEY')
    if not keywords:
        return []
    if key:
        q = urllib.parse.quote(' '.join(keywords[:3]))
        try:
            data = _http_json(
                f'http://api.mediastack.com/v1/news?access_key={key}&keywords={q}&limit=10&sort=published_desc',
                timeout=12,
            )
            arts = data.get('data', []) if isinstance(data, dict) else []
            return [{
                'title': (a.get('title') or '')[:200],
                'source': a.get('source', ''),
                'url': a.get('url', ''),
                'published': (a.get('published_at') or '')[:10],
                'snippet': (a.get('description') or '')[:250],
            } for a in arts[:6]]
        except Exception as e:
            return [{'error': f'Mediastack failed: {e}'}]
    # Free fallback: GNews RSS (Google News)
    try:
        q = urllib.parse.quote(' '.join(keywords[:2]))
        body = _http(f'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en', timeout=10).decode('utf-8', errors='replace')
        items = re.findall(r'<item>(.*?)</item>', body, re.DOTALL)[:6]
        out = []
        for it in items:
            title = re.search(r'<title>(.*?)</title>', it)
            link = re.search(r'<link>(.*?)</link>', it)
            pub = re.search(r'<pubDate>(.*?)</pubDate>', it)
            src = re.search(r'<source[^>]*>(.*?)</source>', it)
            out.append({
                'title': unescape(title.group(1)) if title else '',
                'url': link.group(1) if link else '',
                'published': pub.group(1)[:25] if pub else '',
                'source': unescape(src.group(1)) if src else 'Google News',
            })
        return out
    except Exception as e:
        return [{'error': f'GNews fallback failed: {e}'}]


# ---------------------------------------------------------------------------
# LOCAL harvesters
# ---------------------------------------------------------------------------

def _parse_skill_md(skill_md: Path, keywords: list[str]) -> dict | None:
    try:
        text = skill_md.read_text(errors='ignore')
    except Exception:
        return None
    if keywords and not any(kw in text.lower() for kw in keywords):
        return None
    desc = ''
    for line in text.splitlines()[:30]:
        line = line.strip()
        if line.lower().startswith('description:'):
            desc = line.split(':', 1)[1].strip().strip('"\'')
            break
    return {'description': desc[:240]}


def harvest_skills(keywords: list[str]) -> list[dict]:
    matches: list[dict] = []
    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / 'SKILL.md'
            if not skill_md.exists():
                continue
            parsed = _parse_skill_md(skill_md, keywords)
            if parsed is None:
                continue
            matches.append({'name': skill_dir.name, 'description': parsed['description'],
                           'invoke': f"/{skill_dir.name}", 'origin': 'user-skills'})
            if len(matches) >= 25:
                return matches
    if PLUGINS_DIR.exists():
        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            skills_root = plugin_dir / 'skills'
            if not skills_root.exists():
                continue
            for skill_dir in sorted(skills_root.iterdir()):
                skill_md = skill_dir / 'SKILL.md'
                if not skill_md.exists():
                    continue
                parsed = _parse_skill_md(skill_md, keywords)
                if parsed is None:
                    continue
                matches.append({'name': skill_dir.name, 'description': parsed['description'],
                               'invoke': f"/{plugin_dir.name}:{skill_dir.name}",
                               'origin': f'plugin:{plugin_dir.name}'})
                if len(matches) >= 25:
                    return matches
    return matches


def harvest_agents(keywords: list[str]) -> list[dict]:
    matches: list[dict] = []
    if not AGENTS_DIR.exists():
        return matches
    for agent_md in sorted(AGENTS_DIR.glob('*.md')):
        parsed = _parse_skill_md(agent_md, keywords)
        if parsed is None:
            continue
        matches.append({'name': agent_md.stem, 'description': parsed['description']})
        if len(matches) >= 20:
            break
    return matches


def harvest_local_tools() -> list[dict]:
    candidates = [
        ('ffmpeg', 'video/audio mux + transcode'),
        ('whisper-cli', 'local speech-to-text (whisper.cpp)'),
        ('piper', 'local neural TTS'),
        ('gh', 'GitHub CLI'),
        ('rclone', 'cloud sync'),
        ('imagemagick', 'image conversion'),
        ('jq', 'JSON munging'),
        ('yt-dlp', 'video download'),
        ('manim', 'mathematical animations'),
        ('node', 'Node.js runtime'),
        ('pnpm', 'fast Node package manager'),
        ('ollama', 'local LLM runtime'),
        ('exa', 'modern ls'),
        ('fd', 'modern find'),
        ('rg', 'ripgrep search'),
        ('claude', 'Claude Code CLI'),
        ('codex', 'OpenAI Codex CLI'),
        ('python3.13', 'Python 3.13 runtime'),
        ('ssh', 'remote shell to fleet'),
        ('curl', 'HTTP client'),
        ('jq', 'JSON processor'),
    ]
    found: list[dict] = []
    for cli, purpose in candidates:
        path = shutil.which(cli)
        if path:
            found.append({'name': cli, 'path': path, 'purpose': purpose})
    return found


def harvest_ollama_models() -> list[dict]:
    try:
        with urllib.request.urlopen(f'{OLLAMA_HOST}/api/tags', timeout=4) as r:
            data = json.loads(r.read())
        return [{'name': m.get('name', '?'),
                 'size_gb': round(m.get('size', 0) / (1024 ** 3), 1)}
                for m in data.get('models', [])]
    except Exception as e:
        return [{'error': f'Ollama not reachable: {e}'}]


def harvest_fleet() -> list[dict]:
    return [
        {'name': 'Dexter',  'role': 'Senior Dev',    'focus': 'NERVIX backend, CrawdBot, DevOps',  'host': 'dexter'},
        {'name': 'Memo',    'role': 'PM',            'focus': 'MyWork Framework, n8n automations', 'host': 'memo'},
        {'name': 'Sienna',  'role': 'Crypto',        'focus': 'ZmartyChat / smarty.me, trading',   'host': 'sienna'},
        {'name': 'Nano',    'role': 'Agent Creator', 'focus': 'NERVIX agents, enrollment, CLI',    'host': 'nano'},
    ]


def harvest_fleet_live() -> dict:
    """Live availability snapshot via the existing dispatch_task.py + Hermes.
    Tells the synthesizer which specialist actually has bandwidth right now,
    so the 'Fleet Owner' recommendation isn't blind to current workload."""
    try:
        from .fleet_dispatch import fleet_integration_snapshot  # lazy import
        return fleet_integration_snapshot()
    except Exception as e:
        return {'error': f'fleet integration unavailable: {e}'}


# ───────────────────────────────────────────────────────────────────────────
# LOCAL-CONTEXT HARVESTER — pulls from Dan's actual project files when the
# prompt mentions personal/private topics (DansLab, Dan, Nervix, ZmartyChat,
# Dexter/Memo/Sienna/Nano, OpenClaw, paperclip, semeclaw, kryptostack, etc.).
#
# Without this, Perplexity returns 0 results because there's no public web
# data on these private projects — and the synthesis hallucinates.
# ───────────────────────────────────────────────────────────────────────────

LOCAL_TRIGGER_TOKENS = {
    'danslab', 'dan ', 'dans ', 'dansidanutz', 'kryptostack',
    'nervix', 'zmartychat', 'zmarty', 'paperclip', 'semeclaw', 'openclaw',
    'dexter', 'memo', 'sienna', 'nano', 'mywork',
    'mac studio', 'mac mini', 'tailscale', 'fleet',
    'cluj', 'irise coin', "player's poker",
}

LOCAL_CONTEXT_SOURCES = [
    # Lab-wide identity + architecture (highest signal)
    ('SYSTEM',       Path('/Users/davidai/Desktop/DavidAi/SYSTEM.md'),    8000),
    ('LAB_CLAUDE',   Path('/Users/davidai/Desktop/DavidAi/CLAUDE.md'),    6000),
    ('OPERATOR',     Path('/Users/davidai/CLAUDE.md'),                    2000),
    # DansLab project itself (the actual subject)
    ('DANSLAB_README',  Path('/Users/davidai/Desktop/DavidAi/DansLab/README.md'),       3000),
    ('DANSLAB_SYSTEM',  Path('/Users/davidai/Desktop/DavidAi/DansLab/SYSTEM.md'),       6000),
    ('DANSLAB_BACKEND', Path('/Users/davidai/Desktop/DavidAi/DansLab/BACKEND_SETUP.md'),3000),
    # Each major project — README only (avoid bulk)
    ('NERVIX',       Path('/Users/davidai/Desktop/DavidAi/Nervix/README.md'),           3000),
    ('ZMARTYCHAT',   Path('/Users/davidai/Desktop/DavidAi/ZmartyChat/README.md'),       3000),
    ('SEMECLAW',     Path('/Users/davidai/Desktop/DavidAi/SemeClaw/README.md'),         3000),
    ('PAPERCLIP',    Path('/Users/davidai/Desktop/DavidAi/paperclip/README.md'),        3000),
]


def _prompt_triggers_local(prompt_lc: str) -> list[str]:
    """Returns list of triggered tokens — empty list means topic is public."""
    return sorted({t.strip() for t in LOCAL_TRIGGER_TOKENS if t in prompt_lc})


def harvest_local_context(prompt: str) -> list[dict]:
    """If the prompt names a private/personal topic (DansLab, Dan, Nervix...),
    read the actual project files. Otherwise return empty list and let the
    public harvesters do their thing."""
    triggers = _prompt_triggers_local((prompt or '').lower())
    if not triggers:
        return []
    out: list[dict] = []
    for label, path, max_chars in LOCAL_CONTEXT_SOURCES:
        try:
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')[:max_chars]
            if len(text.strip()) < 50:
                continue
            out.append({
                'source': label,
                'path': str(path),
                'chars': len(text),
                'updated': datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                'snippet': text,
            })
        except Exception as e:
            out.append({'source': label, 'path': str(path), 'error': str(e)[:120]})
    # Project directory listing (for visibility)
    try:
        davidai = Path('/Users/davidai/Desktop/DavidAi')
        if davidai.exists():
            projects = sorted([p.name for p in davidai.iterdir()
                              if p.is_dir() and not p.name.startswith('.')
                              and p.name not in ('node_modules', '__pycache__', 'logs', 'tools')])[:30]
            out.append({
                'source': 'PROJECT_INVENTORY',
                'path': str(davidai),
                'projects': projects,
                'count': len(projects),
            })
    except Exception:
        pass
    out.append({'source': '_TRIGGERED_BY', 'tokens': triggers})
    return out


# Need datetime + timezone for the harvester
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Subscription Advisor
# ---------------------------------------------------------------------------

PAID_APIS = [
    {'env': 'EXA_API_KEY',         'name': 'Exa.ai',      'price': '$25-100/mo',
     'value': 'Neural/semantic web search — best-in-class for finding high-quality recent niche content. SimpleQA accuracy 94.9%. Free 100/mo.',
     'priority': 1},
    {'env': 'TAVILY_API_KEY',      'name': 'Tavily',      'price': '$10-30/mo',
     'value': 'Agent-tuned research search with built-in answer + ranked results. 180ms p50. Free 50/mo.',
     'priority': 2},
    {'env': 'FIRECRAWL_API_KEY',   'name': 'Firecrawl',   'price': '$19-49/mo',
     'value': 'Clean Markdown extraction of any page (incl. JS-rendered). Drives multi-pass synthesis. Free 500 credits.',
     'priority': 3},
    {'env': 'MEDIASTACK_API_KEY',  'name': 'Mediastack',  'price': '$25/mo',
     'value': 'Current news API across 7,500+ sources with filters. Cheaper than NewsAPI ($449/mo).',
     'priority': 4},
    {'env': 'SERPAPI_API_KEY',     'name': 'SerpAPI',     'price': '$75/mo',
     'value': 'Real parsed Google SERPs — sitelinks, PAA, dates, ads. Only buy if you need authentic SERP structure.',
     'priority': 5},
    {'env': 'YOU_API_KEY',         'name': 'You.com Research', 'price': '~$0.05/call',
     'value': 'Alternative to Perplexity Deep Research — produces full reports.',
     'priority': 6},
    {'env': 'APIFY_API_TOKEN',     'name': 'Apify',       'price': '$5/mo credit',
     'value': 'Scrape X/Twitter, LinkedIn, YouTube comments — competitive intel from social.',
     'priority': 7},
    {'env': 'KAGI_API_KEY',        'name': 'Kagi Search', 'price': '$25/1k queries',
     'value': 'Privacy-first quality search — clean attribution, no ads.',
     'priority': 8},
]


def subscription_advisor() -> dict:
    """Returns which paid APIs are configured vs which would unlock more value."""
    have = []
    missing = []
    for api in PAID_APIS:
        if _key(api['env']):
            have.append({'name': api['name'], 'env': api['env']})
        else:
            missing.append(api)
    # Always-have: Brave (key present, may be quota-exhausted), Perplexity
    if _key('BRAVE_API_KEY', 'DLS_BRAVE_API_KEY'):
        have.append({'name': 'Brave Search', 'env': 'DLS_BRAVE_API_KEY (note: free tier 2k/mo, often exhausted)'})
    if _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
        have.append({'name': 'Perplexity Sonar', 'env': 'PERPLEXITY_API_KEY'})
    return {'have': have, 'recommend_in_priority_order': missing}


# ---------------------------------------------------------------------------
# Hermes Pre-Route — runs BEFORE the GSD pre-pass so the project context,
# fleet workload, and routing policy are baked into Step 1's input.
# ---------------------------------------------------------------------------

HERMES_PREROUTE_TEMPLATE = """You are Hermes, the master orchestrator on Dan's Lab Mac Studio.
You do NOT answer the user's research request. You ONLY add PROJECT CONTEXT and
ROUTING HINTS that the downstream GSD pre-pass and search engine will then use.

PROJECT CONTEXT (always apply):
- Priority #1: NERVIX (nervix.ai) — the AI agent marketplace
- Fleet specialists: Dexter (Senior Dev — backend, DevOps), Memo (PM — automations, n8n),
                     Sienna (Crypto — ZmartyChat, trading), Nano (Agent Creator — NERVIX agents)
- Banned models: ollama/tinyllama, deepseek/deepseek-chat, gemini-2.0-flash, openai/gpt-5.4-pro
- Cost policy: cheapest capable model first — Ollama local → free OpenRouter → Claude
- NEVER pay-per-usage; only subs (Anthropic, GPT Max, Kimi, GLM) + local + free OR
- Security: never break Telegram bots, never delete repos, never use --no-verify

LIVE FLEET WORKLOAD (right now):
{fleet_workload}

RECENT LEARNINGS (from past Step 1 runs — what worked, what failed). USE THESE to bias your
routing. If a past pattern with a similar prompt scored low, name what to do differently.
{learnings}

USER REQUEST:
{prompt}

Output VALID JSON ONLY. No prose, no code fences. Schema:
{{
  "is_nervix_related": true/false,
  "priority_boost": "<HIGH if NERVIX or critical infra; MEDIUM otherwise>",
  "project_context_to_inject": "<1-2 sentences of project-specific context the GSD pass MUST consider>",
  "fleet_routing_hint": "<which specialist based on FIT + LIVE BANDWIDTH; explain the 'and' between fit and load>",
  "model_policy_implications": "<any model-routing constraints this request triggers>",
  "security_or_infra_concerns": "<flag if this could break Telegram/Redis/balancer/Tailscale>",
  "additional_constraints": ["<extra constraints to bias keywords/exclude lists>", ...],
  "stop_or_proceed": "PROCEED|STOP",
  "stop_reason": "<empty unless STOP>"
}}
"""


def hermes_preroute(prompt: str, fleet_workload_text: str = '') -> dict:
    """Hermes-style pre-route. Adds project context (NERVIX priority, fleet
    state, model policy) so the GSD pre-pass operates with David's lab
    knowledge baked in. If the live HCI is reachable later, this can be
    swapped to call Hermes directly via fleet_dispatch.notify_hermes."""
    # Pull recent learnings so Hermes evolves with each run
    try:
        from .learnings import learnings_for_hermes
        learnings_text = learnings_for_hermes(limit=8)
    except Exception:
        learnings_text = '(learnings store unavailable)'
    payload = HERMES_PREROUTE_TEMPLATE.format(
        prompt=prompt,
        fleet_workload=fleet_workload_text or '(workload unknown)',
        learnings=learnings_text,
    )
    raw = call_ollama(payload, model=LOCAL_SYNTHESIS_MODEL, timeout=90)
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```\s*$', '', raw.strip(), flags=re.MULTILINE)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            spec = json.loads(m.group(0))
            spec.setdefault('is_nervix_related', False)
            spec.setdefault('priority_boost', 'MEDIUM')
            spec.setdefault('stop_or_proceed', 'PROCEED')
            return spec
        except Exception:
            pass
    return {
        'is_nervix_related': 'nervix' in prompt.lower(),
        'priority_boost': 'MEDIUM',
        'project_context_to_inject': '',
        'fleet_routing_hint': '',
        'model_policy_implications': '',
        'security_or_infra_concerns': '',
        'additional_constraints': [],
        'stop_or_proceed': 'PROCEED',
        '_parse_error': 'Hermes pre-route returned non-JSON; using fallback.',
    }


# ---------------------------------------------------------------------------
# Fleet Review — runs AFTER synthesis. Each specialist reviews the brief
# from their domain expertise, in parallel. Their reviews get appended to
# the brief so the user sees a multi-perspective decision-grade output.
# ---------------------------------------------------------------------------

FLEET_REVIEWERS = {
    'dexter': {
        'role': 'Senior Developer (NERVIX backend, CrawdBot, DevOps)',
        'lens': 'technical feasibility, security, performance, integration risk, missing tests',
    },
    'memo': {
        'role': 'Project Manager (MyWork Framework, n8n automations)',
        'lens': 'scope, dependencies, timeline risk, stakeholder alignment, what could slip',
    },
    'sienna': {
        'role': 'Crypto Specialist (ZmartyChat, trading systems)',
        'lens': 'crypto market reality, on-chain implications, regulatory angle, custody risk',
    },
    'nano': {
        'role': 'Agent Creator (NERVIX agents, enrollment, automation)',
        'lens': 'which parts can be turned into reusable agents, automation hooks, agent-economy fit',
    },
}

FLEET_REVIEW_TEMPLATE = """You are {agent_name}, the {role} on Dan's Lab fleet.
Your domain lens: {lens}.

You are reviewing the research brief below for the user's request. Speak ONLY in
your domain — don't restate the brief, don't answer outside your lane. Be direct.
If you have nothing useful to add from your domain, say "Out of scope for this brief."
in one line and stop.

TOPIC KIND: {topic_kind}
{topic_kind_hint}

USER REQUEST:
{prompt}

RESEARCH BRIEF (just synthesized):
{brief}

LIVE FLEET STATUS:
{fleet_status}

Output Markdown ONLY (no preamble, no JSON, no code fences). Use these sections:

### {agent_name} — what I'd add (from my {role_short} lens)
2-4 sharp bullets max. Each bullet: a SPECIFIC concern, missing piece, or angle the brief is weak on.
Cite concrete repos / tools / config from the brief by name where relevant.

### {agent_name} — verdict on whether to proceed
One line: GREEN-LIGHT / YELLOW-LIGHT / RED-LIGHT + one-sentence reason.

### {agent_name} — if I owned this
2-3 lines: what I'd do FIRST as the owner. Concrete next moves only.
"""

PRIVATE_TOPIC_HINT = (
    "PRIVATE — this topic is internal to Dan's Lab (DansLab/Nervix/ZmartyChat/team/architecture). "
    "Public web search returning empty for these terms is EXPECTED. The brief should be grounded "
    "in [LOCAL:*] citations from the actual project files (SYSTEM.md, README.md, etc.). "
    "Do NOT RED-LIGHT just because external/public sources are missing — that is the wrong bar "
    "for a private topic. RED-LIGHT only if the brief contradicts known facts, hallucinates, or "
    "misses a domain-specific concern in YOUR lane."
)
PUBLIC_TOPIC_HINT = (
    "PUBLIC — this is a public/general topic. Brief should cite external sources ([1], [2], etc.) "
    "with recent dates. Apply normal research rigor: external grounding required."
)


def _review_one(agent: str, prompt: str, brief: str, fleet_status: str,
                is_private: bool = False) -> dict:
    info = FLEET_REVIEWERS[agent]
    payload = FLEET_REVIEW_TEMPLATE.format(
        agent_name=agent.capitalize(),
        role=info['role'],
        role_short=info['role'].split('(')[0].strip(),
        lens=info['lens'],
        topic_kind='PRIVATE/INTERNAL' if is_private else 'PUBLIC',
        topic_kind_hint=PRIVATE_TOPIC_HINT if is_private else PUBLIC_TOPIC_HINT,
        prompt=prompt[:600],
        brief=brief[:3500],
        fleet_status=fleet_status[:600] or '(workload unknown)',
    )
    text = call_ollama(payload, model=LOCAL_SYNTHESIS_MODEL, timeout=90)
    return {'agent': agent, 'role': info['role'], 'review': text.strip()}


def fleet_review(prompt: str, brief: str, fleet_workload_text: str = '',
                 is_private: bool = False) -> list[dict]:
    """Run all four specialists' reviews in parallel."""
    reviews: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_review_one, agent, prompt, brief, fleet_workload_text, is_private): agent
            for agent in FLEET_REVIEWERS
        }
        for fut in as_completed(futures):
            try:
                reviews.append(fut.result())
            except Exception as e:
                reviews.append({'agent': futures[fut], 'review': f'_(review failed: {e})_'})
    # Sort by canonical fleet order so output is deterministic
    order = list(FLEET_REVIEWERS.keys())
    reviews.sort(key=lambda r: order.index(r['agent']) if r['agent'] in order else 99)
    return reviews


def consolidate_with_fleet_review(brief: str, reviews: list[dict]) -> str:
    """Append the four specialist reviews + a consensus block to the synthesis."""
    if not reviews:
        return brief
    md = brief.rstrip()
    md += '\n\n---\n\n## 👥 OpenClaw Fleet Review (post-synthesis)\n\n'
    md += '*Each specialist independently reviewed the brief above through their domain lens.*\n\n'
    for r in reviews:
        md += r.get('review', '_(no review)_').strip() + '\n\n'
    # Tally the verdicts
    verdicts = []
    for r in reviews:
        m = re.search(r'(GREEN|YELLOW|RED)-?LIGHT', r.get('review', ''), re.I)
        if m:
            verdicts.append((r['agent'], m.group(1).upper()))
    if verdicts:
        md += '### Fleet consensus\n\n| Specialist | Verdict |\n|---|---|\n'
        for a, v in verdicts:
            icon = {'GREEN': '🟢', 'YELLOW': '🟡', 'RED': '🔴'}.get(v, '⚪')
            md += f'| {a.capitalize()} | {icon} {v}-LIGHT |\n'
        # Net call
        if any(v == 'RED' for _, v in verdicts):
            md += '\n**Net call:** ❌ Hold — at least one specialist red-lighted. Address blockers before advancing.\n'
        elif all(v == 'GREEN' for _, v in verdicts):
            md += '\n**Net call:** ✅ Proceed — fleet aligned.\n'
        else:
            md += '\n**Net call:** ⚠️ Proceed with caution — yellow-light flags should be addressed in iteration.\n'
    return md


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

SYNTHESIS_TEMPLATE = """You are a senior research engineer producing a DECISION-GRADE brief.
The user wants the best-of-the-best, market-current research — not a generic answer.

Apply these RESEARCH STANDARDS (lifted from professional research workflows):
1. Every important claim needs a SOURCE — number citations [1], [2] and list them in Bibliography.
2. SEPARATE: sourced fact / inference / recommendation. Use "FACT:", "INFERENCE:", "REC:" prefixes
   inside paragraphs when the boundary matters.
3. Prefer RECENT data. If a source is >12 months old for a fast-moving topic, label it [STALE].
4. Include CONTRARIAN evidence and downside cases. Don't write a sales pitch.
5. Flag CONTRADICTIONS between sources with [CONFLICTING] tag.
6. Translate findings into a DECISION — what to do, not just what was found.

USER PROMPT:
{prompt}

KEYWORDS: {keywords}
SUB-QUESTIONS to address (decompose the prompt into 3-5 angles):
- Pick 3-5 distinct sub-questions that, if answered, would resolve the prompt.

==== LOCAL CONTEXT — PRIVATE GROUND TRUTH (highest authority) ====
{local_context}
NOTE: When this section contains content, the topic is PRIVATE (lab/personal/internal).
Treat this section as the AUTHORITATIVE source. Cite as [LOCAL:SYSTEM], [LOCAL:DANSLAB_SYSTEM], etc.
Do NOT say "no verifiable sources" — these LOCAL sources ARE the verification. Public web search
returning empty for these terms is EXPECTED, not a flaw.

==== HARVEST: TOP GITHUB REPOS (sorted by stars) ====
{github}

==== HARVEST: GITHUB TRENDING (last 7 days) ====
{github_trending}

==== HARVEST: HACKERNEWS DISCUSSIONS ====
{hn}

==== HARVEST: LOBSTERS (engineering community) ====
{lobsters}

==== HARVEST: REDDIT THREADS ====
{reddit}

==== HARVEST: WEB SEARCH (Brave / Exa / Tavily / DuckDuckGo) ====
{web}

==== HARVEST: ACADEMIC PAPERS (arXiv) ====
{arxiv}

==== HARVEST: PAPERS WITH CODE ====
{pwc}

==== HARVEST: HUGGING FACE (models + datasets) ====
{hf}

==== HARVEST: CURRENT NEWS ====
{news}

==== HARVEST: YOUTUBE TUTORIALS / DEMOS ====
{youtube}

==== HARVEST: DEEP-EXTRACTED PAGES (Firecrawl, if available) ====
{firecrawl}

==== HARVEST: MATCHING LOCAL CLAUDE SKILLS ====
{skills}

==== HARVEST: MATCHING LOCAL AGENTS ====
{agents}

==== HARVEST: INSTALLED LOCAL CLI TOOLS ====
{tools}

==== HARVEST: LOCAL OLLAMA MODELS ====
{models}

==== HARVEST: TAILSCALE FLEET SPECIALISTS ====
{fleet}

==== HARVEST: LIVE FLEET WORKLOAD (Hermes + OpenClaw + dispatch gate) ====
Use this to pick a Fleet Owner who actually has bandwidth right now.
{fleet_live}

==== AVAILABLE PAID APIS (already configured) ====
{have_apis}

==== UNAVAILABLE PAID APIS (would unlock more value) ====
{missing_apis}

Now produce the brief. Use these EXACT headings, in this order. Ground every claim in a citation
[number] that maps to the Bibliography. No preamble, no fluff.

## Verdict
One paragraph. Confidence: LOW / MEDIUM / HIGH. State the recommendation directly.

## Sub-Questions Addressed
List the 3-5 sub-questions you decomposed the prompt into, each with a 1-sentence answer.

## Top 5 Open-Source Adoptions
Numbered list. For each: name + link, why it fits, what to lift, what to AVOID. Cite sources.

## Comparative Matrix
A Markdown table comparing the top options across these columns:
| Option | License | Maturity | Stars | Last Update | Best For | Risk |

## Recommended Local Stack
Concrete, exact identifiers from the harvest above (skill names, CLI names, Ollama model names).
This is what the user should actually run.

## Fleet Owner
Which fleet specialist (Dexter / Memo / Sienna / Nano) should own execution and why.

## Architectural Decisions Locked
Bullet list of decisions this research closes off so we don't relitigate.

## Risks, Gaps & Contradictions
- Risks: what could go wrong
- Gaps: what we still don't know
- Contradictions: where sources disagree, marked [CONFLICTING]

## Recency Audit
For each major source cited, note if it's [FRESH] (<6 months), [OK] (6-12 months), or [STALE] (>12 months).

## Bibliography
Numbered citations matching [N] markers in the body. Include URL + retrieval date if available.

## Subscription Advisor
For each PAID API in "UNAVAILABLE" above, briefly note whether buying it would meaningfully improve THIS specific research, and in what priority order. If we already have everything we need, say so.

## Next Action
ONE concrete next command, PR description, or step the user can execute immediately.
"""


# ---------------------------------------------------------------------------
# GSD Pre-Pass — refines the user's raw prompt into a structured research spec
# before the search engine runs. This is the first half of the iteration loop.
# ---------------------------------------------------------------------------

GSD_PREPASS_TEMPLATE = """You are running the GSD (Get Stuff Done) framework on a research request.
You do NOT answer the request. You ONLY refine it into a structured spec the
downstream search engine can execute well.

GSD framework rules:
1. Clarify INTENT — what kind of decision/output is the user actually after?
2. Decompose into 3-5 SUB-QUESTIONS that, if answered, satisfy the request.
3. Surface implicit CONSTRAINTS (budget, time, stack, license, security).
4. Pick the most specific search KEYWORDS (avoid generic terms like "best", "good").
5. Identify what to EXCLUDE (e.g. "no paid SaaS", "skip pre-2024 results").
6. Suggest a FLEET OWNER from {{Dexter, Memo, Sienna, Nano}} based on the focus area.
7. If iterating, focus on what the user's NOTES asked for — don't redo settled work.

USER REQUEST:
{prompt}

PRIOR BRIEF (from previous iteration; may be empty on first run):
{prior_brief}

USER NOTES on the prior brief (may be empty):
{notes}

Output VALID JSON ONLY — no prose, no code fences. Schema:
{{
  "refined_prompt": "<single clean paragraph restating what the user actually wants>",
  "intent_class": "<one of: research|comparison|adoption-decision|build-vs-buy|architecture|trend-scan>",
  "deliverables": ["<what the final brief MUST contain>", ...],
  "constraints": ["<implicit constraints>", ...],
  "search_keywords": ["<6-8 most specific keywords for harvesters>", ...],
  "sub_questions": ["<3-5 sub-questions to address>", ...],
  "exclude": ["<what to filter OUT>", ...],
  "fleet_owner_hint": "<Dexter|Memo|Sienna|Nano>",
  "iteration_focus": "<if NOTES provided: what specifically to dig deeper this round; else empty string>"
}}
"""


def gsd_prepass(prompt: str, prior_brief: str = '', notes: str = '') -> dict:
    """Run the GSD framework over the user's request. Returns a structured spec."""
    payload = GSD_PREPASS_TEMPLATE.format(
        prompt=prompt,
        prior_brief=(prior_brief or '(none — first iteration)')[:3000],
        notes=(notes or '(none)')[:1200],
    )
    raw = call_ollama(payload, model=LOCAL_SYNTHESIS_MODEL, timeout=120)
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```\s*$', '', raw.strip(), flags=re.MULTILINE)
    # Try to locate the JSON object even if the model added preamble
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            spec = json.loads(m.group(0))
            spec.setdefault('refined_prompt', prompt)
            spec.setdefault('search_keywords', extract_keywords(prompt, limit=8))
            spec.setdefault('sub_questions', [])
            spec.setdefault('intent_class', 'research')
            return spec
        except Exception:
            pass
    return {
        'refined_prompt': prompt,
        'intent_class': 'research',
        'deliverables': [],
        'constraints': [],
        'search_keywords': extract_keywords(prompt, limit=8),
        'sub_questions': [],
        'exclude': [],
        'fleet_owner_hint': '',
        'iteration_focus': notes[:200] if notes else '',
        '_parse_error': 'GSD pre-pass returned non-JSON; using fallback spec.',
    }


def call_ollama(prompt: str, model: str = LOCAL_SYNTHESIS_MODEL, timeout: int = 240) -> str:
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                       'stream': False}).encode('utf-8')
    req = urllib.request.Request(f'{OLLAMA_HOST}/api/chat', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return data.get('message', {}).get('content', '').strip()
    except urllib.error.HTTPError as e:
        return f'_(Ollama HTTP {e.code}: {e.reason})_'
    except urllib.error.URLError as e:
        return f'_(Ollama unreachable at {OLLAMA_HOST}: {e.reason})_'
    except Exception as e:
        return f'_(Ollama call failed: {e})_'


def call_openclaude(prompt: str, timeout: int = 300) -> str:
    """Route inference through Dan's local openclaude CLI (Claude Code with
    DavidAi ECC settings — currently backed by GLM-5.1).

    Trades Ollama's hard 8B-parameter ceiling for GLM-5.1 frontier reasoning
    + 200K context. Used for private-topic synthesis where the local context
    block is the source of truth and qwen3:8b risks hallucination on thin
    spots. Falls back to Ollama on any failure.
    """
    import subprocess
    # Don't merge stderr into stdout — Claude Code emits "Warning: no stdin
    # data received in 3s..." on -i shell functions and that message bleeds
    # into the synthesis output if 2>&1 is used. Capture stderr separately.
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
        # Defensive strip: even with stderr captured separately, some Claude Code
        # versions still write the warning to stdout. Strip it.
        text = re.sub(r'^Warning:[^\n]*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'^If piping from a slow command[^\n]*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*proceeding without it\.[^\n]*\n?', '', text, flags=re.MULTILINE)
        return text.strip()
    except subprocess.TimeoutExpired:
        return f'_(openclaude timed out after {timeout}s)_'
    except Exception as e:
        return f'_(openclaude call failed: {e})_'


def call_perplexity(prompt: str, model: str = DEEP_SYNTHESIS_MODEL, timeout: int = 240) -> str:
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
        content = choices[0].get('message', {}).get('content', '').strip()
        cites = data.get('citations') or []
        if cites:
            content += '\n\n## Citations (Perplexity-supplied)\n' + '\n'.join(f'- {c}' for c in cites[:15])
        return content
    except urllib.error.HTTPError as e:
        return f'_(Perplexity HTTP {e.code}: {e.reason})_'
    except Exception as e:
        return f'_(Perplexity call failed: {e})_'


def _build_local_context_block(harvest: dict) -> tuple[str, bool]:
    """Render harvest['local_context'] into a synthesis-ready block.

    Returns (text, is_private_topic). When is_private_topic is True, callers
    should bypass Perplexity (it dismisses private docs as 'unverified') and
    use a local model with explicit 'treat as ground truth' instructions.
    """
    items = harvest.get('local_context') or []
    if not isinstance(items, list) or not items:
        return '(none — topic appears public; rely on web harvest above)', False
    chunks: list[str] = []
    triggered: list[str] = []
    is_private = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('source') == '_TRIGGERED_BY':
            triggered = item.get('tokens') or []
            continue
        if 'snippet' in item:
            is_private = True
            chunks.append(
                f"### [LOCAL:{item['source']}] {item.get('path','')}\n"
                f"_(updated {item.get('updated','?')}, {item.get('chars','?')} chars)_\n"
                f"{item['snippet']}"
            )
        elif 'projects' in item:
            chunks.append(
                f"### [LOCAL:{item['source']}] project inventory in {item.get('path','')}\n"
                f"{', '.join(item.get('projects', [])[:30])}"
            )
    if not chunks:
        return '(none — topic appears public; rely on web harvest above)', False
    header = ''
    if triggered:
        header = f"_(triggered by tokens: {', '.join(triggered)})_\n\n"
    text = header + '\n\n'.join(chunks)
    # Cap at ~24k chars so the synthesis prompt stays under 60k chars total.
    return text[:24000], is_private


def synthesize(prompt: str, keywords: list[str], harvest: dict, advisor: dict,
               mode: str) -> tuple[str, str]:
    web_results = []
    for src in ('perplexity_search', 'exa', 'tavily', 'brave', 'serpapi', 'duckduckgo'):
        items = harvest.get(src, [])
        if items and isinstance(items, list):
            usable = [r for r in items if isinstance(r, dict) and 'error' not in r and 'skipped' not in r]
            if usable:
                web_results.extend([{'_source': src, **r} for r in usable[:5]])
    local_context_block, is_private_topic = _build_local_context_block(harvest)
    payload = SYNTHESIS_TEMPLATE.format(
        prompt=prompt,
        keywords=', '.join(keywords) or '(none extracted)',
        local_context=local_context_block,
        github=json.dumps(harvest.get('github_repos', []), indent=2)[:2800],
        github_trending=json.dumps(harvest.get('github_trending', []), indent=2)[:1800],
        hn=json.dumps(harvest.get('hackernews', []), indent=2)[:1500],
        lobsters=json.dumps(harvest.get('lobsters', []), indent=2)[:1200],
        reddit=json.dumps(harvest.get('reddit', []), indent=2)[:1500],
        web=json.dumps(web_results, indent=2)[:3000],
        arxiv=json.dumps(harvest.get('arxiv', []), indent=2)[:1500],
        pwc=json.dumps(harvest.get('papers_with_code', []), indent=2)[:1200],
        hf=json.dumps(harvest.get('huggingface', []), indent=2)[:1200],
        news=json.dumps(harvest.get('news', []), indent=2)[:1500],
        youtube=json.dumps(harvest.get('youtube', []), indent=2)[:1200],
        firecrawl=json.dumps(harvest.get('firecrawl', []), indent=2)[:2500],
        skills=json.dumps(harvest.get('skills', []), indent=2)[:1500],
        agents=json.dumps(harvest.get('agents', []), indent=2)[:1500],
        tools=json.dumps(harvest.get('tools', []), indent=2)[:1000],
        models=json.dumps(harvest.get('ollama_models', []), indent=2)[:1000],
        fleet=json.dumps(harvest.get('fleet', []), indent=2),
        fleet_live=json.dumps(harvest.get('fleet_live', {}), indent=2)[:1500],
        have_apis=json.dumps(advisor.get('have', []), indent=2)[:800],
        missing_apis=json.dumps(advisor.get('recommend_in_priority_order', []), indent=2)[:1500],
    )
    # PRIVATE TOPIC ROUTING — when the local-context harvester returned snippets,
    # the topic is internal/personal/lab. Perplexity sonar-pro is web-grounded
    # and CONSISTENTLY dismisses private docs as "unverifiable" — capping the
    # brief at "Confidence: LOW" and triggering RED-light from the fleet.
    #
    # Preferred path: openclaude (Dan's local CLI → GLM-5.1 frontier reasoning,
    # 200K context, no hallucination on thin local context). Fallback: Ollama
    # qwen3:8b for offline guarantee. Set STEP1_PRIVATE_VIA=ollama to skip
    # openclaude (e.g. when CLI is unhealthy) and STEP1_PRIVATE_VIA=openclaude
    # to force it without fallback.
    if is_private_topic:
        via = (os.environ.get('STEP1_PRIVATE_VIA') or 'auto').lower()
        if via != 'ollama':
            text = call_openclaude(payload, timeout=420)
            if text and not text.startswith('_('):
                return text, 'openclaude/glm-5.1 (private-topic route)'
            if via == 'openclaude':
                # Forced openclaude — surface the failure rather than silently fallback
                return text, 'openclaude/glm-5.1 (failed, no fallback)'
        text = call_ollama(payload, model=LOCAL_SYNTHESIS_MODEL, timeout=360)
        return text, f'{LOCAL_SYNTHESIS_MODEL} (private-topic route, ollama fallback)'

    # PUBLIC TOPIC — Perplexity Sonar Pro for search-grounded synthesis.
    if _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
        text = call_perplexity(payload)
        if text and not text.startswith('_('):
            return text, DEEP_SYNTHESIS_MODEL
    return call_ollama(payload), LOCAL_SYNTHESIS_MODEL


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_plan(prompt: str, keywords: list[str], mode: str, advisor: dict) -> dict:
    have_brave = bool(_key('BRAVE_API_KEY', 'DLS_BRAVE_API_KEY'))
    have_pplx = bool(_key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'))
    have_exa = bool(_key('EXA_API_KEY'))
    have_tavily = bool(_key('TAVILY_API_KEY'))
    have_firecrawl = bool(_key('FIRECRAWL_API_KEY'))
    have_serpapi = bool(_key('SERPAPI_API_KEY'))
    have_news = bool(_key('MEDIASTACK_API_KEY'))

    pipeline = [
        f'1. Decompose prompt → keywords: {", ".join(keywords) or "(none)"}',
        '2. GitHub: top repos by stars + 7-day trending',
        '3. Communities: HackerNews, Lobsters, Reddit',
        '4. Academic: arXiv + Papers With Code',
        '5. Models/Datasets: Hugging Face Hub',
        f'6. Web search: {"Exa+Tavily+Brave" if (have_exa or have_tavily) else "Brave+DuckDuckGo"}{" + SerpAPI" if have_serpapi else ""}',
        f'7. Current news: {"Mediastack" if have_news else "Google News RSS (free fallback)"}',
        '8. Video tutorials: YouTube via Piped public API',
        f'9. Page extraction: {"Firecrawl on top URLs" if have_firecrawl else "(skipped — no Firecrawl key)"}',
        '10. Local: skills, agents, CLIs, Ollama models, Tailscale fleet',
        f'11. Synthesize via {"Perplexity " + DEEP_SYNTHESIS_MODEL + " (search-grounded)" if mode=="deep" and have_pplx else "local Ollama " + LOCAL_SYNTHESIS_MODEL}',
        '12. Apply research standards: facts/inference/recs separated, citations, recency, contradictions',
    ]
    return {
        'interpretation': f'Decision-grade research brief for: "{prompt[:240]}"',
        'keywords': keywords,
        'mode': mode,
        'capabilities': {
            'github_search': True, 'github_trending': True, 'hackernews': True,
            'lobsters': True, 'reddit': True, 'arxiv': True,
            'papers_with_code': True, 'huggingface': True, 'youtube': True,
            'duckduckgo': True, 'gnews_rss': True,
            'brave_search': have_brave, 'exa': have_exa, 'tavily': have_tavily,
            'firecrawl': have_firecrawl, 'serpapi': have_serpapi,
            'mediastack_news': have_news, 'perplexity_deep': have_pplx,
            'ollama_local': True,
        },
        'pipeline': pipeline,
        'advisor': advisor,
    }


def run_step1(prompt: str, mode: str = 'fast', prior_brief: str = '',
              notes: str = '', gsd_spec: dict | None = None,
              skip_hermes: bool = False, skip_fleet_review: bool = False,
              max_convergence: int = 2,
              length_seconds: int = 60,
              project: str = 'default') -> dict:
    """Run Step 1 — research + planning. The `length_seconds` parameter is
    CRITICAL: it tells the engine how deep to research. A 40s short needs a
    surface-level brief; a 15-minute documentary needs a deep, multi-act
    research plan with named protagonists, multiple sub-questions, and rich
    evidence. The synthesizer + GSD pre-pass both consume this value."""
    """Run the full Step 1 pipeline:

    Stage 1: HERMES PRE-ROUTE  — adds project context (NERVIX priority,
              fleet workload, model policy, security guards) to the prompt
    Stage 2: GSD PRE-PASS      — refined spec (now informed by Hermes)
    Stage 3: HARVEST           — 17 parallel sources
    Stage 4: SYNTHESIS         — Ollama (fast) or Perplexity (deep)
    Stage 5: FLEET REVIEW      — Dexter + Memo + Sienna + Nano critique
                                 the brief in parallel from their domain
                                 lens; output gets appended + a verdict
                                 tally
    """
    started = time.time()
    stage_times: dict = {}

    # ---------- STAGE 1: HERMES PRE-ROUTE ----------
    t = time.time()
    fleet_live_snapshot = harvest_fleet_live()
    fleet_status_text = ''
    wl = fleet_live_snapshot.get('workload') or {}
    if isinstance(wl, dict) and wl.get('available'):
        fleet_status_text = wl.get('raw_status', '')[:600]
    if skip_hermes:
        hermes = {'stop_or_proceed': 'PROCEED', 'skipped': True}
    else:
        hermes = hermes_preroute(prompt, fleet_workload_text=fleet_status_text)
    stage_times['hermes_preroute'] = round(time.time() - t, 1)

    # If Hermes said STOP, return early with the reason
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {
            'prompt': prompt, 'hermes': hermes, 'stopped': True,
            'stop_reason': hermes.get('stop_reason', 'Hermes blocked the request'),
            'elapsed_seconds': round(time.time() - started, 1),
            'stage_times': stage_times,
        }

    # ---------- STAGE 2: GSD PRE-PASS (with Hermes context) ----------
    t = time.time()
    if gsd_spec is None:
        # Inject Hermes context into the prompt so GSD sees it
        ctx_lines = []
        if hermes.get('project_context_to_inject'):
            ctx_lines.append(f'PROJECT CONTEXT (from Hermes): {hermes["project_context_to_inject"]}')
        if hermes.get('additional_constraints'):
            ctx_lines.append('CONSTRAINTS: ' + '; '.join(hermes['additional_constraints']))
        if hermes.get('fleet_routing_hint'):
            ctx_lines.append(f'FLEET HINT: {hermes["fleet_routing_hint"]}')
        if hermes.get('priority_boost') == 'HIGH':
            ctx_lines.append('PRIORITY: HIGH (per Hermes — likely NERVIX or critical infra)')
        contextualized_prompt = prompt
        if ctx_lines:
            contextualized_prompt = prompt + '\n\n[Hermes injection]\n' + '\n'.join(ctx_lines)
        gsd_spec = gsd_prepass(contextualized_prompt, prior_brief=prior_brief, notes=notes)
    stage_times['gsd_prepass'] = round(time.time() - t, 1)

    refined_prompt = gsd_spec.get('refined_prompt') or prompt
    keywords = gsd_spec.get('search_keywords') or extract_keywords(refined_prompt)
    if not isinstance(keywords, list):
        keywords = extract_keywords(refined_prompt)
    advisor = subscription_advisor()
    plan = build_plan(refined_prompt, keywords, mode, advisor)
    plan['gsd'] = gsd_spec
    plan['hermes'] = hermes
    plan['iteration'] = bool(prior_brief or notes)

    # SELF-IMPROVEMENT · pull the daily-refreshed OSS registry as a harvest source.
    # Step 1 sees the FULL pipeline catalog (all categories) since research touches
    # every downstream stage — its synthesis must reference what's actually available.
    def _harvest_oss_registry_research():
        try:
            from .discovery import registry_for_steps
            text = registry_for_steps(
                steps=None,  # all steps — research has the full view
                categories=['video-gen','audio-tts','rendering','quality','editing','design'],
                max_tools=18,
            )
            return text or '(registry empty — discovery cron has not run yet)'
        except Exception as e:
            return f'(OSS registry unavailable: {e})'

    harvest: dict = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(harvest_github, keywords):              'github_repos',
            pool.submit(harvest_github_trending, keywords):     'github_trending',
            pool.submit(harvest_hackernews, keywords):          'hackernews',
            pool.submit(harvest_lobsters, keywords):            'lobsters',
            pool.submit(harvest_reddit, keywords):              'reddit',
            pool.submit(harvest_duckduckgo, keywords):          'duckduckgo',
            pool.submit(harvest_arxiv, keywords):               'arxiv',
            pool.submit(harvest_papers_with_code, keywords):    'papers_with_code',
            pool.submit(harvest_huggingface, keywords):         'huggingface',
            pool.submit(harvest_youtube, keywords):             'youtube',
            pool.submit(harvest_news, keywords):                'news',
            pool.submit(harvest_skills, keywords):              'skills',
            pool.submit(harvest_agents, keywords):              'agents',
            pool.submit(harvest_local_tools):                   'tools',
            pool.submit(harvest_ollama_models):                 'ollama_models',
            pool.submit(harvest_fleet):                         'fleet',
            pool.submit(harvest_fleet_live):                    'fleet_live',
            pool.submit(harvest_local_context, prompt):         'local_context',
            pool.submit(_harvest_oss_registry_research):        'oss_registry',
        }
        if _key('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY'):
            futures[pool.submit(harvest_perplexity_search, keywords, refined_prompt)] = 'perplexity_search'
        if _key('BRAVE_API_KEY', 'DLS_BRAVE_API_KEY'):
            futures[pool.submit(harvest_brave, keywords)]                  = 'brave'
        if _key('EXA_API_KEY'):
            futures[pool.submit(harvest_exa, keywords, refined_prompt)]    = 'exa'
        if _key('TAVILY_API_KEY'):
            futures[pool.submit(harvest_tavily, keywords, refined_prompt)] = 'tavily'
        if _key('SERPAPI_API_KEY'):
            futures[pool.submit(harvest_serpapi, keywords)]                = 'serpapi'
        if _key('BRIGHTDATA_API_TOKEN', 'BRIGHTDATA_API_KEY'):
            futures[pool.submit(harvest_brightdata, keywords, refined_prompt)] = 'brightdata'
        for fut in as_completed(futures):
            try:
                harvest[futures[fut]] = fut.result()
            except Exception as e:
                harvest[futures[fut]] = [{'error': str(e)}]

    # Firecrawl second pass: deep-extract top URLs from the best web search results
    if _key('FIRECRAWL_API_KEY'):
        top_urls: list[str] = []
        for src in ('exa', 'tavily', 'brave', 'serpapi', 'brightdata'):
            for item in (harvest.get(src) or []):
                if isinstance(item, dict) and item.get('url'):
                    top_urls.append(item['url'])
                if len(top_urls) >= 3:
                    break
            if len(top_urls) >= 3:
                break
        harvest['firecrawl'] = harvest_firecrawl(top_urls)

    # Inject the GSD spec + iteration notes into the synthesis prompt so the
    # synthesizer biases its brief toward what the user asked to dig deeper on.
    synth_prompt = refined_prompt

    # CRITICAL · target video length frames every other decision in the brief.
    # 40s short = surface-level, 1 protagonist, 1 stat. 15min documentary =
    # multi-act story, 4-6 named characters, 12+ research questions, deep evidence.
    minutes = round(length_seconds / 60, 1)
    if length_seconds <= 50:
        depth_hint = f'40-second YouTube Short. ONE hook stat, ONE protagonist, ≤70 narration words. Surface-level research is fine.'
    elif length_seconds <= 90:
        depth_hint = f'1-minute quick explainer. 1 protagonist, 1 conflict, 1 breakthrough, ≤110 narration words.'
    elif length_seconds <= 200:
        depth_hint = f'3-minute feature explainer. Named protagonist, named conflict, 4-5 evidence points, ≤350 narration words. Research must support 12 beats.'
    elif length_seconds <= 400:
        depth_hint = f'5-minute deep dive. Full 3-act arc, named team, architecture, products, ≤600 narration words. Research must surface enough material for 18 beats with continuity.'
    elif length_seconds <= 700:
        depth_hint = f'10-minute long-form documentary. Multi-act narrative, named characters, full company architecture, products, goals, evidence, ≤1200 narration words. Research must support 24-30 beats.'
    else:
        depth_hint = f'15-minute full feature. Documentary depth: meet the protagonist, the team, the architecture, the products, the conflict, the breakthrough, the evidence, the goals, the call to action. ≤1800 narration words. Research must support 36+ beats with rich continuity, named characters, and quantified targets.'
    synth_prompt += f'\n\n## TARGET VIDEO LENGTH · {minutes} min ({length_seconds}s)\n{depth_hint}\n'

    if gsd_spec.get('sub_questions'):
        synth_prompt += '\n\nSUB-QUESTIONS (from GSD pre-pass):\n' + '\n'.join(
            f'- {q}' for q in gsd_spec['sub_questions'])
    if gsd_spec.get('constraints'):
        synth_prompt += '\n\nCONSTRAINTS (from GSD pre-pass):\n' + '\n'.join(
            f'- {c}' for c in gsd_spec['constraints'])

    # CRITICAL · inject local context (DansLab/Nervix/etc.) when the harvester
    # detected personal triggers. This is the source-of-truth for private topics.
    local_ctx = harvest.get('local_context') or []
    if isinstance(local_ctx, list) and local_ctx:
        readable = []
        for item in local_ctx:
            if isinstance(item, dict) and 'snippet' in item:
                readable.append(f"### {item['source']} ({item['path']})\n{item['snippet']}")
            elif isinstance(item, dict) and 'projects' in item:
                readable.append(f"### {item['source']}\nProjects in /Desktop/DavidAi: {', '.join(item.get('projects', [])[:30])}")
        if readable:
            synth_prompt += '\n\n## LOCAL CONTEXT — THE GROUND TRUTH ABOUT THIS PRIVATE TOPIC\n'
            synth_prompt += '\n(These are the actual project files for the topic. Use them as the primary source. Do NOT hallucinate facts — if not here, say "unknown".)\n\n'
            synth_prompt += '\n\n'.join(readable)[:14000]

    if notes:
        synth_prompt += f'\n\nUSER NOTES on prior iteration (focus here): {notes[:400]}'
    if prior_brief:
        synth_prompt += f'\n\nPRIOR BRIEF EXCERPT (do not repeat verbatim, build on it):\n{prior_brief[:800]}'

    # ---------- STAGE 4: SYNTHESIS ----------
    t = time.time()
    synthesis, model_used = synthesize(synth_prompt, keywords, harvest, advisor, mode)
    stage_times['synthesis'] = round(time.time() - t, 1)

    # Detect private/internal topic (DansLab/Nervix/etc.) so fleet review uses the
    # right rubric — public sources are NOT the right bar for a private topic.
    _, is_private_topic = _build_local_context_block(harvest)

    # ---------- STAGE 5: CONVERGENCE LOOP — re-synth if any reviewer red-lights
    # ---------- STAGE 5a: FLEET REVIEW (Dexter / Memo / Sienna / Nano in parallel)
    fleet_reviews: list[dict] = []
    convergence_passes = 0
    max_convergence_passes = max(0, int(max_convergence))
    if not skip_fleet_review:
        while convergence_passes <= max_convergence_passes:
            t = time.time()
            fleet_reviews = fleet_review(refined_prompt, synthesis, fleet_status_text,
                                         is_private=is_private_topic)
            stage_times[f'fleet_review_pass_{convergence_passes+1}'] = round(time.time() - t, 1)
            verdicts = []
            for r in fleet_reviews:
                m = re.search(r'(GREEN|YELLOW|RED)-?LIGHT', r.get('review', ''), re.I)
                if m:
                    verdicts.append(m.group(1).upper())
            has_red = any(v == 'RED' for v in verdicts)
            if not has_red or convergence_passes >= max_convergence_passes:
                break
            # RED-LIGHT detected — fold the reviewer feedback into the synthesis prompt
            # and re-synth so the next pass actively addresses the blockers.
            convergence_passes += 1
            critique_block = '\n\n## CONVERGENCE PASS — fleet flagged RED. Address these blockers in the next brief:\n'
            for r in fleet_reviews:
                if 'RED' in r.get('review', '').upper():
                    critique_block += f'\n### {r["agent"].capitalize()}:\n{r["review"][:1500]}\n'
            synth_prompt_v2 = synth_prompt + critique_block
            t = time.time()
            synthesis, model_used = synthesize(synth_prompt_v2, keywords, harvest, advisor, mode)
            stage_times[f'synthesis_pass_{convergence_passes+1}'] = round(time.time() - t, 1)

    # Final brief = synthesis + consolidated fleet review at the end
    final_synthesis = consolidate_with_fleet_review(synthesis, fleet_reviews) if fleet_reviews else synthesis

    rating = compute_quality_rating(
        hermes=hermes, gsd_spec=gsd_spec, harvest=harvest,
        synthesis=final_synthesis, fleet_reviews=fleet_reviews,
        convergence_passes=convergence_passes,
    )

    result = {
        'prompt': prompt,
        'refined_prompt': refined_prompt,
        'hermes': hermes,
        'gsd': gsd_spec,
        'plan': plan,
        'harvest': harvest,
        'synthesis': final_synthesis,
        'synthesis_raw': synthesis,
        'fleet_reviews': fleet_reviews,
        'convergence_passes': convergence_passes,
        'quality_rating': rating,
        'model': model_used,
        'mode': mode,
        'advisor': advisor,
        'iteration': bool(prior_brief or notes),
        'stage_times': stage_times,
        'elapsed_seconds': round(time.time() - started, 1),
    }

    # ─── SELF-IMPROVEMENT · persist run outcome for next runs ─────────────
    # Every run records: prompt, stars, model, fleet verdicts, what worked,
    # what failed. The Hermes pre-route reads recent records via
    # learnings_for_hermes() to avoid repeating mistakes.
    try:
        from .learnings import record_learning
        what_worked: list[str] = []
        what_failed: list[str] = []
        # Detect successes
        usable_sources = rating.get('usable_sources', 0)
        if usable_sources >= 10: what_worked.append(f'broad harvest: {usable_sources} sources')
        if rating.get('synthesis_chars', 0) >= 3000: what_worked.append('rich synthesis')
        fv = rating.get('fleet_verdicts', {})
        if fv.get('GREEN', 0) == 4: what_worked.append('fleet aligned (4 green)')
        # Detect failures (from rating reasons + fleet timeouts)
        for reason in rating.get('reasons', []):
            if any(s in reason for s in ['❌', '⚠️', 'failed', 'timed out', 'Thin', 'Light harvest', 'Only', 'missing']):
                what_failed.append(reason[:140])
        record = {
            'step': 1,
            'kind': 'research',
            'prompt': prompt[:400],
            'refined_prompt': refined_prompt[:400] if refined_prompt and refined_prompt != prompt else None,
            'mode': mode,
            'model': model_used,
            'stars': rating.get('stars'),
            'advance_ok': rating.get('advance_ok'),
            'fleet_verdicts': fv,
            'usable_sources': usable_sources,
            'synthesis_chars': rating.get('synthesis_chars', 0),
            'convergence_passes': convergence_passes,
            'elapsed_seconds': round(time.time() - started, 1),
            'what_worked': what_worked,
            'what_failed': what_failed,
            'iteration': bool(prior_brief or notes),
            'user_notes': (notes or '')[:300],
        }
        record_learning(record)
    except Exception:
        pass  # learning store is best-effort; never block the user's run

    # Spec scoring (1-10 cumulative, +1 per locked step, 8+ to advance)
    try:
        from .scoring import lock_step_from_run
        lock_step_from_run(
            project=project, step=1,
            fleet={'verdicts': rating.get('fleet_verdicts', {})},
            stars=rating.get('stars', 0.0),
            convergence_passes=convergence_passes,
            notes=rating.get('label', ''),
        )
    except Exception:
        pass

    # Spec: "After Hermes gave the qualifying score for next step, a skill-step1
    # is created and added to Skill database" — auto-register on high-quality runs.
    try:
        from .skill_db import register_skill
        _excerpt = {
            'refined_prompt': refined_prompt[:300] if refined_prompt else '',
            'usable_sources': rating.get('usable_sources', 0),
            'synthesis_chars': rating.get('synthesis_chars', 0),
            'fleet_verdicts': rating.get('fleet_verdicts', {}),
        }
        _summary = f"step1 research · {(rating.get('label') or '')[:80]}"
        register_skill(
            step=1, prompt=prompt,
            stars=rating.get('stars', 0.0),
            summary=_summary,
            result_excerpt=_excerpt,
        )
        from .learnings import generate_skill_md
        generate_skill_md(step_num=1, prompt=prompt, summary=_summary,
                          result_excerpt=_excerpt, stars=rating.get('stars', 0.0))
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Advice generator — diagnose weaknesses and produce focused refinement notes
# that the iteration loop can re-run with. Powers the gold "Give me Advice"
# auto-loop button.
# ---------------------------------------------------------------------------

ADVICE_TEMPLATE = """You are a senior research coach. The user just produced this Step 1 brief
on the Zmarty Video Production pipeline. The brief scored {stars}/5 — below the 5★
threshold needed to advance.

Your job is NOT to write a new brief. You write FOCUSED REFINEMENT NOTES that, when
fed back into the iteration loop, will trigger a re-run that addresses the weaknesses.

ORIGINAL PROMPT:
{prompt}

CURRENT QUALITY RATING:
- Stars: {stars}/5  ({label})
- Why it's low (reasons):
{reasons}
- Fleet verdicts: GREEN={green}, YELLOW={yellow}, RED={red}

FLEET REVIEWER CONCERNS (full text):
{reviews}

THE CURRENT BRIEF (for context — don't repeat it, build on it):
{brief}

Generate the refinement notes. Rules:
1. Address EVERY RED-LIGHT first (these are vetoes — they must be resolved).
2. Then YELLOW flags in priority order.
3. If harvest coverage was light, name SPECIFIC additional search angles or sub-questions.
4. Be SPECIFIC — don't say "be more thorough"; name the missing concept (e.g. "regulatory
   compliance for YouTube content automation", "performance benchmarks for TTS on M-series").
5. Keep the original intent — don't redirect the topic, just sharpen the focus.
6. Notes must be readable verbatim by the GSD pre-pass — write them as if the user typed them.

Output VALID JSON ONLY (no prose, no code fences):
{{
  "diagnosis": "<1-2 sentence diagnosis of what went wrong>",
  "focused_notes": "<the actual textarea content — 3-6 sentences, ready to be pasted into the iteration notes box>",
  "specific_angles": ["<3-5 search angles to add this round>", ...],
  "exclude_now": ["<what to actively rule out / filter>", ...],
  "expected_lift": "<conservative estimate, e.g. '+1.0 to +1.5 stars'>"
}}
"""


def step1_advise(result: dict) -> dict:
    """Given a Step 1 result, generate advice + focused refinement notes.
    The notes are designed to be auto-pasted into the iteration loop's
    textarea so the next pass directly attacks the weaknesses."""
    rating = result.get('quality_rating', {}) or {}
    reasons_block = '\n'.join(f'  • {r}' for r in (rating.get('reasons') or [])) or '  (none recorded)'
    fleet = rating.get('fleet_verdicts') or {}
    reviews = result.get('fleet_reviews') or []
    reviews_block = ''
    for r in reviews[:4]:
        reviews_block += f'\n--- {r.get("agent", "?").upper()} ({r.get("role", "")}) ---\n{r.get("review", "")[:1500]}\n'
    payload = ADVICE_TEMPLATE.format(
        stars=rating.get('stars', '?'),
        label=rating.get('label', ''),
        prompt=(result.get('prompt') or '')[:500],
        reasons=reasons_block,
        green=fleet.get('GREEN', 0),
        yellow=fleet.get('YELLOW', 0),
        red=fleet.get('RED', 0),
        reviews=reviews_block[:6000],
        brief=(result.get('synthesis_raw') or result.get('synthesis') or '')[:4000],
    )
    raw = call_ollama(payload, model=LOCAL_SYNTHESIS_MODEL, timeout=120)
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```\s*$', '', raw.strip(), flags=re.MULTILINE)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            spec = json.loads(m.group(0))
            spec.setdefault('focused_notes', '')
            spec.setdefault('specific_angles', [])
            spec.setdefault('exclude_now', [])
            spec.setdefault('diagnosis', '')
            spec.setdefault('expected_lift', '')
            return spec
        except Exception:
            pass
    # Fallback — heuristic notes built from reasons + verdicts
    parts = []
    for r in (rating.get('reasons') or []):
        parts.append(f'Address: {r}')
    for r in reviews:
        if 'RED' in (r.get('review', '').upper()):
            parts.append(f'{r.get("agent","").capitalize()} red-lighted — resolve their concerns directly.')
    return {
        'diagnosis': 'Auto-fallback advice (model returned non-JSON).',
        'focused_notes': '\n'.join(parts) or 'Strengthen harvest coverage and address all yellow/red fleet flags.',
        'specific_angles': [],
        'exclude_now': [],
        'expected_lift': '+0.5 to +1.0 stars (conservative)',
    }


def step1_post_research(result: dict, user_notes: str = '') -> dict:
    """After Step 1 advance, distill and persist research learnings.

    Step 1 already records a raw run record inside run_step1(); this explicit
    postmortem mirrors steps 2-10 so the dashboard and server can treat every
    step consistently after a lock attempt.
    """
    rating = result.get('quality_rating') or {}
    harvest = result.get('harvest') or {}
    gsd = result.get('gsd') or {}
    fleet = rating.get('fleet_verdicts') or {}
    reasons = rating.get('reasons') or []
    usable_sources = rating.get('usable_sources')
    if usable_sources is None:
        usable_sources = len(harvest.get('sources') or harvest.get('search_results') or [])

    what_worked: list[str] = []
    what_failed: list[str] = []
    if rating.get('stars', 0) >= 5.0:
        what_worked.append('research brief reached the perfection gate')
    if usable_sources:
        what_worked.append(f'{usable_sources} usable research source(s)')
    if fleet.get('GREEN', 0):
        what_worked.append(f"{fleet.get('GREEN', 0)} fleet reviewer(s) green")
    for reason in reasons:
        text = str(reason)
        if any(token in text.lower() for token in ('red', 'missing', 'thin', 'failed', 'weak', 'timeout')):
            what_failed.append(text[:180])
    if user_notes:
        what_failed.append(f'user note: {user_notes[:180]}')

    record = {
        'kind': 'step1_postmortem',
        'step': 1,
        'prompt': result.get('prompt'),
        'refined_prompt': result.get('refined_prompt'),
        'intent_class': gsd.get('intent_class') or gsd.get('topic_class'),
        'stars': rating.get('stars'),
        'quality_rating': rating,
        'fleet_verdicts': fleet,
        'convergence_passes': result.get('convergence_passes'),
        'usable_sources': usable_sources,
        'what_worked': what_worked,
        'what_failed': what_failed,
        'upgrade_search_angles': [
            'new public data providers for the topic',
            'new video research or citation extraction tools',
            'new MCP connectors that can improve source harvesting',
        ],
        'user_notes': user_notes,
    }
    try:
        from .learnings import record_learning
        record_learning(record)
    except Exception:
        pass
    return record


# ---------------------------------------------------------------------------
# Quality rating — does the brief give us a 4-5 star base to advance to Step 2?
# ---------------------------------------------------------------------------

def compute_quality_rating(*, hermes: dict, gsd_spec: dict, harvest: dict,
                           synthesis: str, fleet_reviews: list[dict],
                           convergence_passes: int) -> dict:
    """Returns {stars: float (1.0-5.0), label: str, reasons: [str], advance_ok: bool}."""
    score = 5.0
    reasons: list[str] = []

    # ---- Hermes signal ----
    if hermes.get('stop_or_proceed', 'PROCEED').upper() == 'STOP':
        return {'stars': 1.0, 'label': '🔴 Hermes blocked',
                'reasons': [f'Hermes STOP: {hermes.get("stop_reason","")}'],
                'advance_ok': False}
    # Hermes returns 'security_or_infra_concerns' as a free-form field. Many
    # responses use the field to AFFIRM there are no concerns (e.g., "No risk
    # of breaking Telegram..."). Only deduct when the field actually flags
    # something — i.e. it is a non-empty positive concern, not a negation.
    # Different models return this field as different types — string most often,
    # but GLM-5.1 has been observed returning bool false. Coerce to string first.
    _sc_raw = hermes.get('security_or_infra_concerns')
    if _sc_raw is True:
        sec_concern = ''  # bool true alone tells us nothing concrete; treat as unset
    elif _sc_raw is False or _sc_raw is None:
        sec_concern = ''
    else:
        sec_concern = str(_sc_raw).strip()
    sec_lc = sec_concern.lower()
    is_negation = (
        not sec_concern
        or sec_lc in {'none', 'n/a', 'no concerns', 'no concern', 'no risk', 'none.'}
        or sec_lc.startswith(('no risk', 'no concerns', 'no concern', 'no specific', 'none —', 'none -', 'none,'))
        or 'no security' in sec_lc[:40]
        or 'no infra' in sec_lc[:40]
        or 'no risk of' in sec_lc[:40]
    )
    if sec_concern and not is_negation:
        score -= 0.5
        reasons.append(f'⚠️ Hermes flagged security/infra: {sec_concern[:120]}')

    # ---- GSD spec quality ----
    if not gsd_spec.get('sub_questions'):
        score -= 0.75
        reasons.append('GSD did not produce sub-questions')
    elif len(gsd_spec.get('sub_questions', [])) < 3:
        score -= 0.25
        reasons.append('Only <3 sub-questions in GSD spec')
    if not gsd_spec.get('constraints'):
        score -= 0.25
        reasons.append('GSD did not extract constraints')

    # ---- Harvest coverage ----
    usable_sources = 0
    for src_key in ('github_repos', 'github_trending', 'hackernews', 'lobsters',
                    'reddit', 'arxiv', 'papers_with_code', 'huggingface',
                    'youtube', 'news', 'duckduckgo', 'brave', 'exa',
                    'tavily', 'serpapi', 'skills', 'agents', 'tools',
                    'ollama_models'):
        items = harvest.get(src_key) or []
        if isinstance(items, list):
            real = [i for i in items if isinstance(i, dict) and 'error' not in i and 'skipped' not in i and i]
            if real:
                usable_sources += 1

    # PRIVATE-TOPIC ADJUSTMENT — each local context file (SYSTEM.md, DANSLAB_SYSTEM.md, ...)
    # is a primary source for a private topic. Public web returning empty is EXPECTED.
    # Count each [LOCAL:*] file with a real snippet as a source so the brief isn't
    # unfairly penalized for being grounded in private docs (its only correct grounding).
    local_ctx_items = harvest.get('local_context') or []
    local_sources = 0
    if isinstance(local_ctx_items, list):
        for it in local_ctx_items:
            if isinstance(it, dict) and 'snippet' in it and len((it.get('snippet') or '').strip()) >= 100:
                local_sources += 1
    is_private_topic = local_sources > 0
    if is_private_topic:
        usable_sources += local_sources
        reasons.append(f'🔒 Private topic — {local_sources} local-context files as primary sources')

    # Threshold check — same gate but local sources now count.
    if usable_sources < 6:
        score -= 1.5
        reasons.append(f'Only {usable_sources} usable sources (public + local combined)')
    elif usable_sources < 10:
        score -= 0.5
        reasons.append(f'Light coverage ({usable_sources} sources combined)')

    # ---- Synthesis depth ----
    syn_chars = len(synthesis or '')
    if syn_chars < 1200:
        score -= 1.0
        reasons.append(f'Thin synthesis ({syn_chars} chars)')
    elif syn_chars < 2200:
        score -= 0.25
        reasons.append(f'Synthesis is on the short side ({syn_chars} chars)')
    # Bonus for hitting the required structural sections
    required_sections = ['Verdict', 'Top', 'Comparative', 'Recommended', 'Fleet Owner',
                         'Architectural', 'Risks', 'Bibliography', 'Next Action']
    sections_hit = sum(1 for s in required_sections if s.lower() in (synthesis or '').lower())
    if sections_hit < 5:
        score -= 0.75
        reasons.append(f'Synthesis missing structural sections ({sections_hit}/{len(required_sections)} present)')

    # ---- Fleet review verdicts ----
    # CRITICAL: Detect when fleet review FAILED (timeouts/errors). If reviews
    # exist but none produced a verdict, that's a quality FAILURE, not "all clear".
    verdict_counts = {'GREEN': 0, 'YELLOW': 0, 'RED': 0}
    timeout_count = 0
    error_count = 0
    for r in fleet_reviews or []:
        review_text = r.get('review', '') or ''
        # Detect failures: "_(Ollama call failed: timed out)_", "_(Ollama HTTP ...)_", "_(review failed: ...)_"
        if review_text.startswith('_(') and ('failed' in review_text.lower() or 'timed out' in review_text.lower() or 'unreachable' in review_text.lower()):
            if 'timed out' in review_text.lower():
                timeout_count += 1
            else:
                error_count += 1
            continue
        m = re.search(r'(GREEN|YELLOW|RED)-?LIGHT', review_text, re.I)
        if m:
            verdict_counts[m.group(1).upper()] += 1
    n_reviews = len(fleet_reviews or [])
    n_failed = timeout_count + error_count
    n_graded = sum(verdict_counts.values())
    if n_reviews > 0 and n_graded == 0:
        # ALL fleet reviews failed → cannot grade → FAIL HARD, never above 2.5★
        score = min(score, 2.5) - 0.5
        reasons.append(f'⚠️ Fleet review failed completely ({timeout_count} timeouts, {error_count} errors) — rating capped at 2.0★. Cannot advance until fleet review succeeds.')
    elif n_failed >= 2:
        # Most fleet reviews failed → cap at 3.0
        score = min(score, 3.0)
        reasons.append(f'⚠️ {n_failed}/{n_reviews} fleet reviewers failed (timeouts/errors) — partial review only')
    elif n_failed == 1:
        score -= 0.5
        reasons.append(f'1 fleet reviewer failed ({n_graded}/{n_reviews} successfully reviewed)')
    if verdict_counts['RED'] >= 2:
        score -= 2.0
        reasons.append(f'❌ {verdict_counts["RED"]} fleet reviewers RED-lighted')
    elif verdict_counts['RED'] == 1:
        score -= 1.0
        reasons.append('1 fleet reviewer RED-lighted (single-veto)')
    elif verdict_counts['YELLOW'] >= 2:
        score -= 0.5
        reasons.append(f'{verdict_counts["YELLOW"]} fleet reviewers raised yellow flags')
    if verdict_counts['GREEN'] == 4:
        reasons.append('🟢 All 4 fleet reviewers green-lit')

    # ---- Convergence cost ----
    if convergence_passes >= 2:
        score -= 0.5
        reasons.append(f'Took {convergence_passes} convergence passes to clear red-light')
    elif convergence_passes == 1:
        score -= 0.25
        reasons.append('Took 1 convergence pass to clear red-light')

    # Round to nearest 0.5, clamp
    score = max(1.0, min(5.0, round(score * 2) / 2))

    if score >= 5.0:
        label = '🟢 Perfect base — advance to Step 2'
    elif score >= 4.0:
        label = '🟡 Strong base — refine to 5★ before Step 2'
    elif score >= 3.0:
        label = '🟡 Mixed — refine with notes before advancing'
    elif score >= 2.0:
        label = '🟠 Weak — substantial gaps; iterate further'
    else:
        label = '🔴 Insufficient — re-prompt or check infra'

    return {
        'stars': score,
        'label': label,
        'reasons': reasons,
        'advance_ok': score >= 5.0,
        'usable_sources': usable_sources,
        'synthesis_chars': syn_chars,
        'fleet_verdicts': verdict_counts,
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: step1_research.py [--deep] "your research prompt"')
        sys.exit(1)
    args = sys.argv[1:]
    mode = 'fast'
    if args and args[0] == '--deep':
        mode = 'deep'
        args = args[1:]
    result = run_step1(' '.join(args), mode=mode)
    print(json.dumps(result, indent=2))
