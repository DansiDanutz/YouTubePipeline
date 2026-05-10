#!/usr/bin/env python3.13
"""Dashboard server: serves the static dashboard files AND exposes the
Step 1 research engine as a JSON API at /api/step1/run.

Usage:
  python3.13 server.py [PORT]   # PORT default 8766

Replaces a plain `python3 -m http.server` so we can also accept POSTs.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Hydrate os.environ from macOS Keychain + fleet.env BEFORE any engine module
# imports. Each engine reads its own _key() helper via os.environ.get() at
# import time, so this populates the environment they'll see.
try:
    from engines._secrets import hydrate_environ as _hydrate_secrets, status_snapshot as _secrets_snapshot  # noqa: E402
    from engines._secrets import resolve as _resolve_secret  # noqa: E402
    _SECRETS_HYDRATED = _hydrate_secrets()
    if _SECRETS_HYDRATED:
        print(f'[secrets] hydrated {len(_SECRETS_HYDRATED)} env vars from keychain/fleet.env: '
              f'{sorted(_SECRETS_HYDRATED.keys())}', file=sys.stderr)
except Exception as _hydrate_err:
    _SECRETS_HYDRATED = {}
    _resolve_secret = lambda *names, default='': default
    print(f'[secrets] hydration failed: {_hydrate_err}', file=sys.stderr)

from engines.step1_research import run_step1, gsd_prepass, step1_advise, step1_post_research  # noqa: E402
from engines.step2_script import run_step2, step2_advise, step2_post_research  # noqa: E402
from engines.step3_visual import (  # noqa: E402
    run_step3, step3_advise, step3_post_research,
    export_design_system, render_live_preview,
)
from engines.step4_scenes import run_step4, step4_advise, step4_post_research  # noqa: E402
from engines.step5_audio import run_step5, step5_advise, step5_post_research  # noqa: E402
from engines.step6_subtitles import run_step6, step6_advise, step6_post_research  # noqa: E402
from engines.step7_render import run_step7, step7_advise, step7_post_research  # noqa: E402
from engines.step8_qa import run_step8, step8_advise, step8_post_research  # noqa: E402
from engines.step9_final import run_step9, step9_advise, step9_post_research  # noqa: E402
from engines.step10_addons import run_step10, step10_advise, step10_post_research  # noqa: E402
from engines.scoring import (  # noqa: E402
    get_score, reset_score, record_step_lock, can_advance, history_summary,
)
from engines.skill_db import (  # noqa: E402
    find_skill, register_skill, list_skills, delete_skill, db_summary as skill_db_summary,
)
from engines.scheduler import (  # noqa: E402
    start_daily_discovery_thread, scheduler_status, force_discovery_now,
)
from engines.step_image_gen import image_provider_status, video_provider_status  # noqa: E402
from engines.seedance import status as seedance_status  # noqa: E402
from engines.step5_audio import _have_elevenlabs_key  # noqa: E402
from engines.opencode_client import status as opencode_status  # noqa: E402
from engines.mcp_status import status as mcp_status  # noqa: E402
from engines.discovery import run_discovery, registry_status  # noqa: E402
from engines.fleet_dispatch import (  # noqa: E402
    dispatch_to_agent, notify_hermes, fleet_integration_snapshot,
)
from engines.learnings import (  # noqa: E402
    record_learning, recent_learnings, learnings_summary,
)
from engines.projects import (  # noqa: E402
    list_projects, get_active_id, set_active_id, create_project,
    get_project, update_project_state, rename_project, delete_project,
    projects_summary,
)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
HOST = os.environ.get('ZMARTY_DASHBOARD_HOST', '127.0.0.1')
DEBUG_ERRORS = os.environ.get('ZMARTY_DEBUG_ERRORS', '').lower() in {'1', 'true', 'yes'}
ALLOWED_ORIGIN_HOSTS = {'localhost', '127.0.0.1', '::1'}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # Quieter logs — only show non-2xx and POSTs.
    def log_message(self, format: str, *args):
        msg = format % args
        if ' 200 ' in msg or ' 304 ' in msg:
            if 'POST' not in msg:
                return
        sys.stderr.write(f'[{self.log_date_time_string()}] {msg}\n')

    def end_headers(self):
        # Disable browser caching — the dashboard markup changes often
        # during development, and stale cache was hiding new features.
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _providers_status_payload(self) -> dict:
        # Resolve keys from BOTH os.environ AND ~/.openclaw/fleet.env, matching
        # the engines' _key() helper. Without this, status would falsely report
        # "configured=False" for keys that live only in fleet.env.
        # Match ${VAR}, ${VAR:-}, ${VAR:-default} — quoted or unquoted. These are
        # unresolved shell placeholders (typically from 1Password / keychain
        # injection on the source machine) and must not be treated as real keys.
        import re as _re
        _PLACEHOLDER_RE = _re.compile(
            r'^["\']?\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}["\']?$'
        )

        def _from_fleet_or_env(*names: str) -> str:
            for n in names:
                v = os.environ.get(n) or ''
                if v and not _PLACEHOLDER_RE.fullmatch(v):
                    return v
            try:
                fe = Path.home() / '.openclaw' / 'fleet.env'
                if not fe.exists():
                    return ''
                wanted = set(names)
                for raw in fe.read_text(encoding='utf-8', errors='replace').splitlines():
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export '):
                        line = line[len('export '):]
                    if '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    if k.strip() in wanted:
                        v = v.strip()
                        if _PLACEHOLDER_RE.fullmatch(v):
                            continue  # placeholder; treat as not-set
                        return v.strip('"').strip("'")
            except Exception:
                pass
            return ''

        try:
            from engines import fal_client as _fal_mod
            _fal_status = _fal_mod.status()
        except Exception as e:
            _fal_status = {'configured': False, 'error': str(e)}
        try:
            _or_key = _from_fleet_or_env('OPENROUTER_API_KEY', 'OPENROUTER_KEY',
                                         'DLS_OPENROUTER_API_KEY')
            try:
                from engines import oc_runner as _oc_runner
                _oc_status = _oc_runner.status()
            except Exception:
                _oc_status = {}
            _byok_snap = _secrets_snapshot([
                'ANTHROPIC_API_KEY', 'HERMES_ANTHROPIC_API_KEY',
                'OPENAI_API_KEY', 'MOONSHOT_API_KEY', 'KIMI_MOONSHOT_API_KEY',
                'ZAI_AUTH_TOKEN', 'GLM_API_KEY', 'NVIDIA_API_KEY',
            ])
            _has = lambda name: bool(_byok_snap.get(name, {}).get('configured'))
            _byok_local = {
                'anthropic': _has('ANTHROPIC_API_KEY') or _has('HERMES_ANTHROPIC_API_KEY'),
                'openai': _has('OPENAI_API_KEY'),
                'moonshot': _has('MOONSHOT_API_KEY') or _has('KIMI_MOONSHOT_API_KEY'),
                'z-ai': _has('ZAI_AUTH_TOKEN') or _has('GLM_API_KEY'),
                'nvidia': _has('NVIDIA_API_KEY'),
            }
            _or_status = {
                **_oc_status,
                'configured':    bool(_or_key),
                'verdict_model': _from_fleet_or_env('STEP9_VERDICT_MODEL')
                                 or _oc_status.get('default_model')
                                 or 'anthropic/claude-sonnet-4.6',
                'byok_local_keys': _byok_local,
                'byok_local_count': sum(1 for v in _byok_local.values() if v),
                'byok_note': 'OpenRouter BYOK keys must be configured in the OpenRouter account; request routing is set to prioritize BYOK-capable providers.',
            }
        except Exception:
            _or_status = {'configured': False}
        # Perplexity (Step 1 deep search) — surface its presence too
        try:
            _pplx_key = _from_fleet_or_env('PERPLEXITY_API_KEY', 'DLS_PERPLEXITY_API_KEY')
            _pplx_status = {'configured': bool(_pplx_key)}
        except Exception:
            _pplx_status = {'configured': False}
        try:
            import urllib.request as _urlreq
            _ollama_host = os.environ.get('OLLAMA_HOST', 'http://localhost:11434').rstrip('/')
            with _urlreq.urlopen(f'{_ollama_host}/api/tags', timeout=3) as _r:
                _tags = json.loads(_r.read().decode('utf-8'))
            _models = [m.get('name') or m.get('model') for m in _tags.get('models', []) if isinstance(m, dict)]
            _ollama_status = {'available': True, 'host': _ollama_host, 'models': _models}
        except Exception as e:
            _ollama_status = {'available': False, 'error': str(e)}

        image = image_provider_status()
        video = video_provider_status()
        image_chain = image.get('chain', []) if isinstance(image, dict) else []
        video_chain = video.get('chain', []) if isinstance(video, dict) else []

        def _as_provider_list(status: dict, chain: list) -> list[dict]:
            providers = []
            for name in chain:
                item = status.get(name, {}) if isinstance(status, dict) else {}
                if isinstance(item, dict):
                    providers.append({'name': name, **item})
            return providers

        # Truth-board: per-secret snapshot (configured? source? length?) — never
        # echoes values. Drives the dashboard's accurate provider grid.
        try:
            secrets_truth = _secrets_snapshot([
                'OPENROUTER_API_KEY', 'OPENROUTER_PROVIDER_ORDER', 'OPENROUTER_BYOK_MAXIMIZE',
                'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'PERPLEXITY_API_KEY',
                'ELEVENLABS_API_KEY', 'BRIGHTDATA_API_TOKEN', 'GLM_API_KEY',
                'TAVILY_API_KEY', 'EXA_API_KEY', 'FIRECRAWL_API_KEY',
                'GEMINI_API_KEY', 'DASHSCOPE_API_KEY', 'NVIDIA_API_KEY',
                'HERMES_ANTHROPIC_API_KEY', 'OPENCLAW_GATEWAY_TOKEN',
                'MOONSHOT_API_KEY', 'KIMI_MOONSHOT_API_KEY',
                'GITHUB_TOKEN', 'NOTION_API_KEY',
                'FAL_API_KEY', 'HIGGSFIELD_API_KEY', 'HIGGSFIELD_API_KEY_SECRET', 'HIGGSFIELD_AUTH',
                'SEEDANCE_API_KEY',
                'OLLAMA_API_KEY', 'DLS_TELEGRAM_BOT_TOKEN',
                'FLEET_DEXTER_BOT_TOKEN', 'FLEET_MEMO_BOT_TOKEN',
                'FLEET_SIENNA_BOT_TOKEN', 'FLEET_NANO_BOT_TOKEN',
                'FLEET_HERMES_BOT_TOKEN',
            ])
            secrets_summary = {
                'total':       len(secrets_truth),
                'configured':  sum(1 for s in secrets_truth.values() if s.get('configured')),
                'by_source':   {
                    src: sum(1 for s in secrets_truth.values() if s.get('source') == src)
                    for src in ('env', 'windows-env', 'keychain', 'fleet.env')
                },
            }
        except Exception as e:
            secrets_truth = {}
            secrets_summary = {'error': str(e)}

        return {
            'image': image,
            'video': video,
            'image_chain': image_chain,
            'video_chain': video_chain,
            'image_providers': _as_provider_list(image, image_chain),
            'video_providers': _as_provider_list(video, video_chain),
            'seedance': seedance_status(),
            'fal': _fal_status,
            'openrouter': _or_status,
            'ollama': _ollama_status,
            'perplexity': _pplx_status,
            'elevenlabs': {'configured': _have_elevenlabs_key()},
            'opencode': opencode_status(),
            'mcp': mcp_status(),
            'scheduler': scheduler_status(),
            'skills': skill_db_summary(),
            'secrets': secrets_truth,
            'secrets_summary': secrets_summary,
        }

    def _origin_allowed(self) -> bool:
        origin = self.headers.get('Origin')
        if not origin:
            return True
        try:
            parsed = urlparse(origin)
            return parsed.scheme in {'http', 'https'} and parsed.hostname in ALLOWED_ORIGIN_HOSTS
        except Exception:
            return False

    def _send_cors_headers(self) -> None:
        origin = self.headers.get('Origin')
        if origin and self._origin_allowed():
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._send_cors_headers()
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(length).decode('utf-8') if length else '{}'
        return json.loads(raw)

    def _generate_piper_audio(self, script: str) -> dict:
        """Run piper TTS on the script. Writes the WAV to dashboard/tmp/audio/
        and returns a URL the frontend can stream."""
        import hashlib
        import shutil
        import subprocess
        # Locate piper binary
        piper = shutil.which('piper') or shutil.which('piper.exe')
        bundled_piper = ROOT.parent / 'tools' / 'piper' / 'piper.exe'
        if not piper and bundled_piper.exists():
            piper = str(bundled_piper)
        if not piper:
            return {'available': False, 'reason': 'piper CLI not installed or not on PATH'}
        # Find the voice model — preference order
        candidates = [
            ROOT.parent / 'tools' / 'piper' / 'en_US-lessac-medium.onnx',
            ROOT.parent / 'voices' / 'en_US-lessac-medium.onnx',
            Path('/Users/davidai/Zmarty-Video-Pipeline/tools/piper/en_US-lessac-medium.onnx'),
            Path('/Users/davidai/Zmarty-Video-Pipeline/voices/en_US-lessac-medium.onnx'),
            Path('/opt/homebrew/share/piper/voices/en_US-lessac-medium.onnx'),
        ]
        voice = next((p for p in candidates if p.exists()), None)
        if not voice:
            return {'available': False, 'reason': f'voice model not found in {[str(p) for p in candidates]}',
                    'hint': 'download from https://huggingface.co/rhasspy/piper-voices'}
        # Cache by script hash so re-renders are instant
        script_hash = hashlib.sha256(script.encode('utf-8')).hexdigest()[:12]
        out_dir = ROOT / 'tmp' / 'audio'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f'preview_{script_hash}.wav'
        if not out_file.exists():
            proc = subprocess.run(
                [piper, '--model', str(voice), '--output_file', str(out_file)],
                input=script.encode('utf-8'),
                capture_output=True, timeout=60,
            )
            if proc.returncode != 0:
                return {'available': False, 'reason': f'piper failed: {proc.stderr.decode("utf-8", errors="replace")[:200]}'}
        return {
            'available': True,
            'url': f'/tmp/audio/{out_file.name}',
            'voice': 'en_US-lessac-medium',
            'cached': out_file.exists(),
        }

    def do_GET(self) -> None:
        path = self.path.rstrip('/')
        if path == '/api/providers/status':
            self._json(200, self._providers_status_payload())
            return
        if path == '/api/discovery/status':
            try:
                self._json(200, registry_status())
            except Exception as e:
                self._json(500, {'error': str(e)})
            return
        if path == '/api/discovery/run':
            try:
                result = run_discovery(verbose=False)
                self._json(200, result)
            except Exception as e:
                payload = {'error': str(e)}
                if DEBUG_ERRORS:
                    payload['trace'] = traceback.format_exc()[:2000]
                self._json(500, payload)
            return
        # Fall through to static file serving
        super().do_GET()

    def do_POST(self) -> None:
        path = self.path.rstrip('/')
        try:
            if path == '/api/step1/gsd':
                body = self._read_body()
                prompt = (body.get('prompt') or '').strip()
                if not prompt:
                    self._json(400, {'error': 'prompt is required'})
                    return
                spec = gsd_prepass(
                    prompt,
                    prior_brief=body.get('prior_brief', ''),
                    notes=body.get('notes', ''),
                )
                self._json(200, {'gsd': spec})
                return
            if path == '/api/step1/run':
                body = self._read_body()
                prompt = (body.get('prompt') or '').strip()
                mode = body.get('mode', 'fast')
                if not prompt:
                    self._json(400, {'error': 'prompt is required'})
                    return
                result = run_step1(
                    prompt,
                    mode=mode,
                    prior_brief=body.get('prior_brief', ''),
                    notes=body.get('notes', ''),
                    gsd_spec=body.get('gsd_spec') or None,
                    max_convergence=int(body.get('max_convergence', 2)),
                    length_seconds=int(body.get('length_seconds', 60)),
                    project=body.get('project') or 'default',
                )
                self._json(200, result)
                return
            if path == '/api/step1/advise':
                body = self._read_body()
                result = body.get('result') or {}
                if not result:
                    self._json(400, {'error': 'result is required'})
                    return
                self._json(200, step1_advise(result))
                return
            if path == '/api/step1/post_research':
                body = self._read_body()
                result = body.get('result') or {}
                if not result:
                    self._json(400, {'error': 'result is required'})
                    return
                self._json(200, step1_post_research(result, body.get('user_notes', '') or body.get('notes', '')))
                return
            if path == '/api/step2/run':
                body = self._read_body()
                user_input = (body.get('user_input') or body.get('prompt') or '').strip()
                if not user_input:
                    self._json(400, {'error': 'user_input is required'})
                    return
                self._json(200, run_step2(
                    user_input,
                    mode=body.get('mode', 'fast'),
                    step1_brief=body.get('step1_brief', ''),
                    prior_script=body.get('prior_script', ''),
                    notes=body.get('notes', ''),
                    max_convergence=int(body.get('max_convergence', 2)),
                    length_seconds=int(body.get('length_seconds', 40)),
                    project=body.get('project') or 'default',
                ))
                return
            if path == '/api/step2/advise':
                body = self._read_body()
                result = body.get('result') or {}
                if not result:
                    self._json(400, {'error': 'result is required'})
                    return
                self._json(200, step2_advise(result))
                return
            if path == '/api/step2/post_research':
                body = self._read_body()
                result = body.get('result') or {}
                if not result:
                    self._json(400, {'error': 'result is required'})
                    return
                self._json(200, step2_post_research(result, body.get('user_notes', '')))
                return
            if path == '/api/step3/run':
                body = self._read_body()
                self._json(200, run_step3(
                    step1_brief=body.get('step1_brief', ''),
                    step2_script=body.get('step2_script', ''),
                    mode=body.get('mode', 'fast'),
                    prior_design=body.get('prior_design', ''),
                    notes=body.get('notes', ''),
                    project=body.get('project') or 'default',
                ))
                return
            if path == '/api/step3/advise':
                body = self._read_body()
                result = body.get('result') or {}
                if not result:
                    self._json(400, {'error': 'result is required'})
                    return
                self._json(200, step3_advise(result))
                return
            if path == '/api/step3/post_research':
                body = self._read_body()
                result = body.get('result') or {}
                if not result:
                    self._json(400, {'error': 'result is required'})
                    return
                self._json(200, step3_post_research(result, body.get('user_notes', '')))
                return
            if path == '/api/step3/export':
                body = self._read_body()
                ds = body.get('design_system') or {}
                if not ds:
                    self._json(400, {'error': 'design_system is required'})
                    return
                self._json(200, export_design_system(ds))
                return
            if path == '/api/step3/preview':
                body = self._read_body()
                ds = body.get('design_system') or {}
                if not ds:
                    self._json(400, {'error': 'design_system is required'})
                    return
                html = render_live_preview(ds, script=body.get('script', ''),
                                           step1_brief=body.get('step1_brief', ''))
                payload = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(payload)
                return
            if path == '/api/step4/run':
                body = self._read_body()
                design_system = body.get('design_system') or {}
                step2_script = (body.get('step2_script') or '').strip()
                result = run_step4(
                    step2_script=step2_script,
                    design_system=design_system,
                    mode=body.get('mode', 'fast'),
                    notes=(body.get('notes') or '').strip(),
                    prior_manifest=body.get('prior_manifest'),
                    max_convergence=int(body.get('max_convergence', 2)),
                    project=body.get('project') or 'default',
                    length_seconds=int(body.get('length_seconds', 40)),
                    scene_seconds=int(body.get('scene_seconds', 15)),
                )
                self._json(200, result)
                return
            if path == '/api/step4/advise':
                body = self._read_body()
                self._json(200, step4_advise(body.get('result') or {}))
                return
            if path == '/api/step4/post_research':
                body = self._read_body()
                self._json(200, step4_post_research(
                    body.get('result') or {},
                    user_notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/step5/run':
                body = self._read_body()
                result = run_step5(
                    script=(body.get('script') or body.get('step2_script') or '').strip(),
                    scene_manifest=body.get('scene_manifest') or {},
                    mode=body.get('mode', 'fast'),
                    notes=(body.get('notes') or '').strip(),
                    prior_spec=body.get('prior_spec') or body.get('prior_audio_spec'),
                    max_convergence=int(body.get('max_convergence', 2)),
                    project=body.get('project') or 'default',
                    voice_preference=(body.get('voice_preference') or body.get('voice') or 'brian').strip(),
                )
                self._json(200, result)
                return
            if path == '/api/step5/advise':
                body = self._read_body()
                self._json(200, step5_advise(body.get('result') or {}))
                return
            if path == '/api/step5/post_research':
                body = self._read_body()
                self._json(200, step5_post_research(
                    body.get('result') or {},
                    user_notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/step6/run':
                body = self._read_body()
                result = run_step6(
                    script=(body.get('step2_script') or '').strip(),
                    audio_spec=body.get('audio_spec') or {},
                    mode=body.get('mode', 'fast'),
                    notes=(body.get('notes') or '').strip(),
                    prior_subtitle_spec=body.get('prior_subtitle_spec'),
                    max_convergence=int(body.get('max_convergence', 2)),
                    project=body.get('project') or 'default',
                    language=(body.get('language') or 'en').strip(),
                )
                self._json(200, result)
                return
            if path == '/api/step6/advise':
                body = self._read_body()
                self._json(200, step6_advise(body.get('result') or {}))
                return
            if path == '/api/step6/post_research':
                body = self._read_body()
                self._json(200, step6_post_research(
                    body.get('result') or {},
                    user_notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/step7/run':
                body = self._read_body()
                # generate_motion_clips: True forces clip stage, False disables,
                # None (default) = auto via STEP7_MOTION_CLIPS env (default 'auto')
                _gen_clips = body.get('generate_motion_clips', None)
                if isinstance(_gen_clips, str):
                    _gen_clips = _gen_clips.lower() in ('1', 'true', 'yes', 'on')
                result = run_step7(
                    scene_manifest=body.get('scene_manifest') or {},
                    audio_spec=body.get('audio_spec') or {},
                    subtitle_spec=body.get('subtitle_spec') or {},
                    subtitles_enabled=bool(body.get('subtitles_enabled', True)),
                    mode=body.get('mode', 'fast'),
                    notes=(body.get('notes') or '').strip(),
                    prior_render_spec=body.get('prior_render_spec'),
                    max_convergence=int(body.get('max_convergence', 2)),
                    project=body.get('project') or 'default',
                    subject=(body.get('subject') or body.get('prompt') or '').strip(),
                    execute=bool(body.get('execute_render', False)),
                    generate_motion_clips=_gen_clips,
                )
                self._json(200, result)
                return
            if path == '/api/step7/advise':
                body = self._read_body()
                self._json(200, step7_advise(body.get('result') or {}))
                return
            if path == '/api/step7/post_research':
                body = self._read_body()
                self._json(200, step7_post_research(
                    body.get('result') or {},
                    user_notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/step8/run':
                body = self._read_body()
                result = run_step8(
                    render_spec=body.get('render_spec') or {},
                    scene_manifest=body.get('scene_manifest') or {},
                    subtitles_enabled=bool(body.get('subtitles_enabled', True)),
                    mode=body.get('mode', 'fast'),
                    notes=(body.get('notes') or '').strip(),
                    prior_qa_spec=body.get('prior_qa_spec'),
                    max_convergence=int(body.get('max_convergence', 2)),
                    project=body.get('project') or 'default',
                )
                self._json(200, result)
                return
            if path == '/api/step8/advise':
                body = self._read_body()
                self._json(200, step8_advise(body.get('result') or {}, body.get('question') or ''))
                return
            if path == '/api/step8/post_research':
                body = self._read_body()
                self._json(200, step8_post_research(
                    body.get('result') or {},
                    notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/step9/run':
                body = self._read_body()
                # use_external_verdict: True forces OpenRouter, False forces
                # local Ollama, None (default) auto-detects via OPENROUTER_API_KEY
                _use_ext = body.get('use_external_verdict', None)
                if isinstance(_use_ext, str):
                    _use_ext = _use_ext.lower() in ('1', 'true', 'yes', 'on')
                result = run_step9(
                    qa_spec=body.get('qa_spec') or {},
                    render_spec=body.get('render_spec') or {},
                    audio_spec=body.get('audio_spec') or {},
                    subtitle_spec=body.get('subtitle_spec') or {},
                    scene_manifest=body.get('scene_manifest') or {},
                    script=(body.get('script') or '').strip(),
                    subtitles_enabled=bool(body.get('subtitles_enabled', True)),
                    mode=body.get('mode', 'fast'),
                    notes=(body.get('notes') or '').strip(),
                    max_convergence=int(body.get('max_convergence', 2)),
                    project=body.get('project') or 'default',
                    use_external_verdict=_use_ext,
                )
                self._json(200, result)
                return
            if path == '/api/step9/advise':
                body = self._read_body()
                self._json(200, step9_advise(body.get('result') or {}, body.get('question') or ''))
                return
            if path == '/api/step9/post_research':
                body = self._read_body()
                self._json(200, step9_post_research(
                    body.get('result') or {},
                    notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/step10/run':
                body = self._read_body()
                self._json(200, run_step10(
                    final_report=body.get('final_report') or {},
                    render_spec=body.get('render_spec') or {},
                    subtitles_enabled=bool(body.get('subtitles_enabled', True)),
                    notes=(body.get('notes') or '').strip(),
                    cumulative_score=int(body.get('cumulative_score', 9)),
                    max_convergence=int(body.get('max_convergence', 1)),
                    project=body.get('project') or 'default',
                ))
                return
            if path == '/api/step10/advise':
                body = self._read_body()
                self._json(200, step10_advise(body.get('result') or {}, body.get('question') or ''))
                return
            if path == '/api/step10/post_research':
                body = self._read_body()
                self._json(200, step10_post_research(
                    body.get('result') or {},
                    notes=(body.get('notes') or ''),
                ))
                return
            if path == '/api/scoring/get':
                body = self._read_body()
                self._json(200, get_score(body.get('project') or 'default'))
                return
            if path == '/api/scoring/summary':
                body = self._read_body()
                self._json(200, history_summary(body.get('project') or 'default'))
                return
            if path == '/api/scoring/reset':
                body = self._read_body()
                self._json(200, reset_score(body.get('project') or 'default'))
                return
            if path == '/api/scoring/lock':
                self._json(403, {
                    'error': 'manual scoring locks are disabled; run the step engine so scoring is derived server-side',
                })
                return
            if path == '/api/scoring/can_advance':
                body = self._read_body()
                self._json(200, can_advance(
                    project=body.get('project') or 'default',
                    predicted_score=body.get('predicted_score'),
                ))
                return
            if path == '/api/skills/find':
                body = self._read_body()
                self._json(200, find_skill(
                    prompt=body.get('prompt') or '',
                    step=int(body.get('step', 1)),
                    threshold=float(body.get('threshold', 0.55)),
                ))
                return
            if path == '/api/skills/register':
                body = self._read_body()
                self._json(200, register_skill(
                    step=int(body.get('step', 0)),
                    prompt=body.get('prompt') or '',
                    stars=float(body.get('stars', 0.0)),
                    summary=body.get('summary') or '',
                    result_excerpt=body.get('result_excerpt') or {},
                ))
                return
            if path == '/api/skills/list':
                body = self._read_body()
                step = body.get('step')
                self._json(200, {'skills': list_skills(
                    step=int(step) if step is not None else None,
                    limit=int(body.get('limit', 100)),
                )})
                return
            if path == '/api/skills/delete':
                body = self._read_body()
                self._json(200, delete_skill(body.get('id') or ''))
                return
            if path == '/api/skills/summary':
                self._json(200, skill_db_summary())
                return
            if path == '/api/scheduler/status':
                self._json(200, scheduler_status())
                return
            if path == '/api/scheduler/force_run':
                self._json(200, force_discovery_now())
                return
            if path == '/api/providers/status':
                # Use the unified helper (reads fleet.env + os.environ for keys)
                self._json(200, self._providers_status_payload())
                return
            if path == '/api/skills/regenerate_md':
                # Backfill SKILL.md files from skill_db.jsonl
                try:
                    from engines.learnings import regenerate_skill_md_from_jsonl
                    self._json(200, regenerate_skill_md_from_jsonl())
                except Exception as e:
                    self._json(500, {'error': str(e)})
                return

            # ── Scene Management v1 — per-scene editing + isolated regen ─────
            # Persistence + per-scene operations live in engines/scene_store.py.
            # Dashboard Scenes panel calls these to edit prompts / re-render
            # one scene at a time, avoiding full-pipeline replay for fixes.
            if path == '/api/scenes/list':
                from engines import scene_store
                body = self._read_body()
                project = (body.get('project') or 'default').strip()
                m = scene_store.load_manifest(project)
                if not m:
                    self._json(404, {'error': f'no manifest for project: {project}'})
                    return
                self._json(200, m)
                return
            if path == '/api/scenes/projects':
                from engines import scene_store
                self._json(200, {'projects': scene_store.list_projects_with_manifests()})
                return
            if path == '/api/scenes/edit':
                from engines import scene_store
                body = self._read_body()
                project = (body.get('project') or 'default').strip()
                sid = (body.get('scene_id') or '').strip()
                patch = body.get('patch') or {}
                if not sid:
                    self._json(400, {'error': 'scene_id is required'})
                    return
                if not isinstance(patch, dict):
                    self._json(400, {'error': 'patch must be a dict'})
                    return
                self._json(200, scene_store.update_scene(project, sid, patch,
                                                        bump_version=False))
                return
            if path == '/api/scenes/regenerate_image':
                from engines import scene_store
                body = self._read_body()
                project = (body.get('project') or 'default').strip()
                sid = (body.get('scene_id') or '').strip()
                if not sid:
                    self._json(400, {'error': 'scene_id is required'})
                    return
                try:
                    result = scene_store.regenerate_scene_image(
                        project=project, scene_id=sid,
                        prompt_override=body.get('prompt_override'),
                        width=int(body.get('width', 1920)),
                        height=int(body.get('height', 1080)),
                    )
                    self._json(200, result)
                except Exception as e:
                    payload = {'error': f'{type(e).__name__}: {e}'}
                    if DEBUG_ERRORS:
                        payload['trace'] = traceback.format_exc()[:2000]
                    self._json(500, payload)
                return
            if path == '/api/scenes/regenerate_motion_clip':
                from engines import scene_store
                body = self._read_body()
                project = (body.get('project') or 'default').strip()
                sid = (body.get('scene_id') or '').strip()
                if not sid:
                    self._json(400, {'error': 'scene_id is required'})
                    return
                try:
                    dur = body.get('duration_s')
                    result = scene_store.regenerate_scene_motion_clip(
                        project=project, scene_id=sid,
                        prompt_override=body.get('prompt_override'),
                        duration_s=float(dur) if dur is not None else None,
                    )
                    self._json(200, result)
                except Exception as e:
                    payload = {'error': f'{type(e).__name__}: {e}'}
                    if DEBUG_ERRORS:
                        payload['trace'] = traceback.format_exc()[:2000]
                    self._json(500, payload)
                return

            if path == '/api/step2/audio':
                # Generate Piper TTS audio for a script and serve as WAV
                body = self._read_body()
                script = (body.get('script') or '').strip()
                if not script:
                    self._json(400, {'error': 'script is required'})
                    return
                try:
                    out = self._generate_piper_audio(script)
                    self._json(200, out)
                except Exception as e:
                    self._json(500, {'error': str(e)})
                return
            if path == '/api/fleet/snapshot':
                self._json(200, fleet_integration_snapshot())
                return
            if path == '/api/learnings/record':
                body = self._read_body()
                self._json(200, record_learning(body))
                return
            if path == '/api/learnings/summary':
                self._json(200, {
                    'summary': learnings_summary(),
                    'recent': recent_learnings(limit=10),
                })
                return
            # ----- Projects (multi-video workspace) -----
            if path == '/api/projects/list':
                self._json(200, {
                    'projects': list_projects(),
                    'active_id': get_active_id(),
                    'summary': projects_summary(),
                })
                return
            if path == '/api/projects/create':
                body = self._read_body()
                self._json(200, create_project(
                    body.get('name', ''), body.get('topic_hint', '')))
                return
            if path == '/api/projects/switch':
                body = self._read_body()
                pid = (body.get('id') or '').strip()
                if not pid:
                    self._json(400, {'error': 'id is required'})
                    return
                self._json(200, set_active_id(pid))
                return
            if path == '/api/projects/get':
                body = self._read_body()
                pid = (body.get('id') or get_active_id() or '').strip()
                if not pid:
                    self._json(400, {'error': 'id is required (or set an active project)'})
                    return
                self._json(200, get_project(pid))
                return
            if path == '/api/projects/state':
                body = self._read_body()
                pid = (body.get('id') or get_active_id() or '').strip()
                if not pid:
                    self._json(400, {'error': 'id is required'})
                    return
                self._json(200, update_project_state(pid, body.get('state') or {}))
                return
            if path == '/api/projects/rename':
                body = self._read_body()
                pid = (body.get('id') or '').strip()
                self._json(200, rename_project(pid, body.get('name', '')))
                return
            if path == '/api/projects/delete':
                body = self._read_body()
                pid = (body.get('id') or '').strip()
                self._json(200, delete_project(pid))
                return
            if path == '/api/fleet/dispatch':
                body = self._read_body()
                agent = (body.get('agent') or '').strip()
                payload = body.get('payload') or ''
                priority = body.get('priority', 'normal')
                dry_run = bool(body.get('dry_run', False))
                self._json(200, dispatch_to_agent(
                    agent, payload, priority=priority, dry_run=dry_run))
                return
            if path == '/api/hermes/notify':
                body = self._read_body()
                self._json(200, notify_hermes(
                    body.get('excerpt') or '', body.get('source') or 'step1'))
                return
            self._json(404, {'error': f'Unknown endpoint: {self.path}'})
        except Exception as e:
            payload = {'error': str(e)}
            if DEBUG_ERRORS:
                payload['trace'] = traceback.format_exc()[:2000]
            self._json(500, payload)


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    # Spec: refresh OSS registry "1 time per day" — daemon thread, idempotent.
    sched = start_daily_discovery_thread()
    with ThreadingTCPServer((HOST, PORT), Handler) as httpd:
        print(f'Dashboard + Step 1 engine on http://{HOST}:{PORT}')
        print(f'  Static files: {ROOT}')
        print(f'  API:          POST /api/step1/run  body: {{"prompt": "...", "mode": "fast|deep"}}')
        print(f'  Scheduler:    {sched}')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nShutting down.')


if __name__ == '__main__':
    main()
