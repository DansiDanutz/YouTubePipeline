#!/usr/bin/env python3.13
"""OpenClaude / OpenRouter LLM runner.

Talks to OpenRouter's chat-completions endpoint directly — same upstream the
`~/.openclaude/oc` bash wrapper points to, but as a thin Python adapter so
pipeline engines can call it programmatically without subprocess overhead.

Why not shell out to `oc`? `oc` is a Mac-side bash wrapper around an npm CLI
that loads settings JSON files and unsets/sets env vars. For our use case
(simple prompt → completion), a direct HTTPS call is faster, more reliable,
and works identically on Windows/Linux/Mac.

Configuration:
  - OPENROUTER_API_KEY  — resolved via _secrets (keychain or env)

Public API:
  - chat(prompt, model=None, system=None, max_tokens=2048) → str
  - chat_json(prompt, model=None, system=None, max_tokens=2048) → dict | None
  - is_configured() → bool
  - status() → dict
  - DEFAULT_MODELS — list of fallback models

Models tier (free → paid quality):
  1. z-ai/glm-4.5-air:free
  2. moonshotai/kimi-k2-instruct
  3. anthropic/claude-3.5-sonnet
  4. openai/gpt-4o-mini
  5. meta-llama/llama-3.3-70b-instruct:free
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

# Default chain — current model IDs as of 2026-05.
# Per spec: GLM, Kimi, or GPT for the Step 9 final verdict.
# Order: paid premier first (best quality), free fallback last.
DEFAULT_MODELS = [
    'z-ai/glm-4.7',                                    # GLM 4.7 — current
    'moonshotai/kimi-k2.6',                            # Kimi K2.6 — current
    'openai/gpt-5-mini',                               # GPT-5 mini — fast, capable
    'anthropic/claude-sonnet-4.6',                     # Claude 4.6 — bonus quality
    'z-ai/glm-4.5-air:free',                           # GLM Air — free fallback
    'nvidia/nemotron-3-super-120b-a12b:free',          # Nemotron — free fallback
    'meta-llama/llama-3.3-70b-instruct:free',          # Llama — free fallback
]

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'


def _key() -> str:
    """Resolve OpenRouter API key via the canonical _secrets path."""
    try:
        from . import _secrets
        return _secrets.resolve('OPENROUTER_API_KEY', 'OPENROUTER_KEY')
    except Exception:
        return os.environ.get('OPENROUTER_API_KEY') or os.environ.get('OPENROUTER_KEY') or ''


def is_configured() -> bool:
    return bool(_key())


def status() -> dict:
    return {
        'configured': is_configured(),
        'default_model': DEFAULT_MODELS[0],
        'fallback_chain': DEFAULT_MODELS,
        'override_env_var': 'STEP9_VERDICT_MODEL or per-call model arg',
    }


def chat(prompt: str, model: str | None = None,
         system: str | None = None,
         max_tokens: int = 2048,
         temperature: float = 0.7,
         timeout_s: int = 90,
         try_chain: bool = False) -> str:
    """Single-shot chat completion. Returns the assistant's text or '' on failure.

    If `try_chain` is True and the configured/preferred model errors, walk
    DEFAULT_MODELS in order. Useful for must-not-fail callers like Step 9 verdict.
    """
    key = _key()
    if not key:
        return ''
    targets = [model] if model else []
    if try_chain or not targets:
        for m in DEFAULT_MODELS:
            if m not in targets:
                targets.append(m)
    last_err = ''
    for m in targets:
        try:
            return _chat_once(prompt, m, system, max_tokens, temperature,
                              timeout_s, key)
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            continue
    return f'_(oc_runner: all models failed; last={last_err})_'


def chat_json(prompt: str, model: str | None = None,
              system: str | None = None,
              max_tokens: int = 2048,
              timeout_s: int = 90,
              try_chain: bool = True) -> dict | None:
    """Chat + extract JSON object from response. Returns None if no parseable JSON."""
    raw = chat(prompt, model=model, system=system, max_tokens=max_tokens,
               temperature=0.2, timeout_s=timeout_s, try_chain=try_chain)
    if not raw or raw.startswith('_('):
        return None
    import re
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```\s*$', '', raw.strip(),
                     flags=re.MULTILINE)
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _chat_once(prompt: str, model: str, system: str | None,
               max_tokens: int, temperature: float,
               timeout_s: int, key: str) -> str:
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})
    body = json.dumps({
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'stream': False,
    }).encode('utf-8')
    req = urllib.request.Request(
        OPENROUTER_URL, data=body,
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type':  'application/json',
            'HTTP-Referer':  'https://zmarty.video',
            'X-Title':       'Zmarty Video Pipeline',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            raw = r.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        # Read the error body to know WHY (rate limit, model gone, etc.)
        body_txt = ''
        try:
            body_txt = e.read().decode('utf-8', errors='replace')[:400]
        except Exception:
            pass
        raise RuntimeError(f'HTTP {e.code} on {model}: {body_txt}') from e
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f'non-JSON response from {model}: {raw[:200]}') from e
    # OpenRouter returns {error: {message, code}} on errors with HTTP 200 sometimes
    if isinstance(data, dict) and data.get('error'):
        err = data['error']
        msg = err.get('message') if isinstance(err, dict) else str(err)
        raise RuntimeError(f'API error on {model}: {msg}')
    choices = data.get('choices') if isinstance(data, dict) else None
    if not choices or not isinstance(choices, list):
        raise RuntimeError(f'no choices in response from {model}: {str(data)[:200]}')
    msg = choices[0].get('message') if isinstance(choices[0], dict) else None
    if not msg or not isinstance(msg, dict):
        raise RuntimeError(f'no message in choice from {model}: {str(choices[0])[:200]}')
    return msg.get('content') or ''


def smoke_test(model: str | None = None) -> dict:
    """Single ping with a tiny prompt — verifies live API access. Returns
    {ok, model, elapsed_s, sample (first 100c)} or {ok: False, error}.
    """
    if not is_configured():
        return {'ok': False, 'error': 'OPENROUTER_API_KEY not configured'}
    t0 = time.time()
    try:
        out = chat('Reply with the single word OK.',
                   model=model or DEFAULT_MODELS[0],
                   max_tokens=10, temperature=0.0, timeout_s=30,
                   try_chain=False)
        elapsed = round(time.time() - t0, 2)
        if out and not out.startswith('_('):
            return {'ok': True, 'model': model or DEFAULT_MODELS[0],
                    'elapsed_s': elapsed, 'sample': out.strip()[:100]}
        return {'ok': False, 'model': model or DEFAULT_MODELS[0],
                'elapsed_s': elapsed, 'error': out[:200]}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'elapsed_s': round(time.time() - t0, 2)}


if __name__ == '__main__':
    print('OpenClaude / OpenRouter runner smoke test')
    print(f'  configured: {is_configured()}')
    if is_configured():
        result = smoke_test()
        print(f'  smoke: {result}')
