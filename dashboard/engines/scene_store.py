#!/usr/bin/env python3.13
"""Per-project scene manifest persistence + per-scene mutation.

Solves the architecture requirement: "each scene needs its own image-gen prompt
+ if something goes bad we want to change only that scene". This module is the
canonical store; every other engine reads/writes scenes through it.

Storage:
  out/projects/<project_id>/scene_manifest.json    → canonical manifest
  out/projects/<project_id>/scenes/scene_<NN>_<id>.jpg   → per-scene hero image
  out/projects/<project_id>/scenes/scene_<NN>_<id>.mp4   → per-scene motion clip

Per-scene record schema:
  {
    "scene_id":         "hook" | "thesis" | "evidence_1" | ... or "scene_007",
    "kind":             "hook|setup|conflict|breakthrough|resolution|cta|...",
    "index":            int (0-based ordinal in the manifest),
    "label":            short human label,
    "duration_seconds": float (story-driven; NOT uniform unless deterministic),
    "frame_start":      int (inclusive),
    "frame_end":        int (exclusive — frame_start of next scene),
    "narration_excerpt":str (the script chunk for this scene),
    "image_prompt":     str (editable — feeds image gen),
    "motion_prompt":    str (editable — feeds img→video),
    "image_path":       str | null (relative to project dir on populate),
    "motion_clip_path": str | null,
    "image_provider":   str | null (which fetcher succeeded last),
    "motion_provider":  str | null,
    "last_regenerated_at": ISO-8601 | null,
    "version":          int (bumps each time the scene is regenerated),
  }

Design principle: the manifest is the **source of truth**. Edits to image
prompts persist through the manifest. Pipeline steps consume the manifest at
runtime — they never override the prompt in-place.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Where finished YouTube video projects live. Each project gets its OWN folder
# named after the subject slug, with all artifacts inside (manifest, scenes,
# audio, subtitles, final mp4, logs). Override via YOUTUBE_VIDEOS_ROOT env so
# Mac runs can drop into a path that's later synced to Windows or vice versa.
def _videos_root() -> Path:
    override = os.environ.get('YOUTUBE_VIDEOS_ROOT', '').strip()
    if override:
        return Path(override).expanduser().resolve()
    # Default: <repo>/Youtube-videos/  (kept inside the pipeline checkout so
    # `git status` shows new videos and we can selectively .gitignore heavy
    # artifacts without losing the manifest)
    return PROJECT_ROOT / 'Youtube-videos'


def _project_dir(project: str) -> Path:
    safe = re.sub(r'[^A-Za-z0-9_\-]', '_', (project or 'default').strip())[:80] or 'default'
    return _videos_root() / safe


def _manifest_path(project: str) -> Path:
    return _project_dir(project) / 'scene_manifest.json'


def _scenes_dir(project: str) -> Path:
    return _project_dir(project) / 'scenes'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ───────────────────────────────────────────────────────────────────────
# Schema normalisation — accept manifests from step4 in either shape
# (list of scenes OR dict keyed by scene_id) and produce a canonical list.
# ───────────────────────────────────────────────────────────────────────

def _normalize_scene(raw: Any, index: int, default_id: Optional[str] = None) -> dict:
    """Coerce a scene record (from any pipeline source) into the canonical shape."""
    if not isinstance(raw, dict):
        raw = {}
    sid = raw.get('scene_id') or raw.get('id') or default_id or f'scene_{index + 1:03d}'
    return {
        'scene_id':            str(sid),
        'kind':                raw.get('kind') or raw.get('story_function') or '',
        'index':               int(raw.get('index', index)),
        'label':               raw.get('label') or raw.get('arc_label') or '',
        'duration_seconds':    float(raw.get('duration_seconds')
                                     or raw.get('duration')
                                     or 0.0),
        'frame_start':         int(raw.get('frame_start', 0)),
        'frame_end':           int(raw.get('frame_end', 0)),
        'narration_excerpt':   raw.get('narration_excerpt') or raw.get('narration') or '',
        'image_prompt':        raw.get('image_prompt') or raw.get('prompt') or '',
        'motion_prompt':       raw.get('motion_prompt') or '',
        'image_path':          raw.get('image_path') or raw.get('hero_image') or None,
        'motion_clip_path':    raw.get('motion_clip_path') or raw.get('motion_clip') or None,
        'image_provider':      raw.get('image_provider'),
        'motion_provider':     raw.get('motion_provider'),
        'last_regenerated_at': raw.get('last_regenerated_at'),
        'version':             int(raw.get('version', 0)),
        # Preserve any non-schema fields (e.g. component_type, jsx_blueprint)
        # so other engines that care about them still work
        **{k: v for k, v in raw.items() if k not in {
            'scene_id', 'id', 'kind', 'story_function', 'index', 'label',
            'arc_label', 'duration_seconds', 'duration', 'frame_start', 'frame_end',
            'narration_excerpt', 'narration', 'image_prompt', 'prompt',
            'motion_prompt', 'image_path', 'hero_image', 'motion_clip_path',
            'motion_clip', 'image_provider', 'motion_provider',
            'last_regenerated_at', 'version',
        }},
    }


def normalize_manifest(raw: dict, project: str = 'default') -> dict:
    """Produce the canonical manifest envelope from a Step 4 output."""
    raw = raw or {}
    raw_scenes = raw.get('scenes')
    scene_records: list[dict] = []
    if isinstance(raw_scenes, list):
        for i, s in enumerate(raw_scenes):
            scene_records.append(_normalize_scene(s, i))
    elif isinstance(raw_scenes, dict):
        for i, (sid, s) in enumerate(raw_scenes.items()):
            rec = _normalize_scene(s, i, default_id=sid)
            scene_records.append(rec)
    return {
        'project':        project,
        'subject':        raw.get('subject') or raw.get('topic') or '',
        'length_seconds': int(raw.get('total_seconds') or raw.get('length_seconds') or 0),
        'fps':            int(raw.get('fps', 30)),
        'total_frames':   int(raw.get('total_frames', 0)),
        'scene_count':    len(scene_records),
        'scenes':         scene_records,
        'design_system':  raw.get('design_system') or {},
        'composition_id': raw.get('composition_id', ''),
        'saved_at':       _now(),
        'manifest_version': int(raw.get('manifest_version', 1)),
    }


# ───────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────

def save_manifest(manifest: dict, project: str = 'default') -> dict:
    """Atomically persist the manifest. Returns {ok, path, scene_count}."""
    d = _project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    canon = normalize_manifest(manifest, project=project)
    p = _manifest_path(project)
    tmp = p.with_suffix('.json.tmp')
    try:
        tmp.write_text(json.dumps(canon, indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(p)
        return {'ok': True, 'path': str(p), 'scene_count': canon['scene_count']}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def load_manifest(project: str = 'default') -> Optional[dict]:
    p = _manifest_path(project)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def get_scene(project: str, scene_id: str) -> Optional[dict]:
    m = load_manifest(project)
    if not m:
        return None
    for s in m.get('scenes', []):
        if s.get('scene_id') == scene_id:
            return s
    return None


def list_scenes(project: str = 'default') -> list[dict]:
    m = load_manifest(project)
    return list(m.get('scenes', [])) if m else []


def update_scene(project: str, scene_id: str, patch: dict,
                 bump_version: bool = True) -> dict:
    """Merge `patch` fields into one scene and persist.

    Returns {ok, scene} on success, {ok: False, error} otherwise.
    `bump_version` increments the per-scene version counter so the renderer
    can know to re-process this scene only.
    """
    m = load_manifest(project)
    if not m:
        return {'ok': False, 'error': f'no manifest for project: {project}'}
    found = None
    for s in m.get('scenes', []):
        if s.get('scene_id') == scene_id:
            found = s
            break
    if not found:
        return {'ok': False, 'error': f'scene_id not in manifest: {scene_id}'}
    # Whitelist editable fields — protect timing/index from accidental mutation
    EDITABLE = {
        'image_prompt', 'motion_prompt', 'narration_excerpt',
        'duration_seconds', 'image_path', 'motion_clip_path',
        'image_provider', 'motion_provider', 'kind', 'label',
    }
    for k, v in (patch or {}).items():
        if k in EDITABLE:
            found[k] = v
    if bump_version:
        found['version'] = int(found.get('version', 0)) + 1
        found['last_regenerated_at'] = _now()
    save_manifest(m, project=project)
    return {'ok': True, 'scene': found}


def scene_image_path(project: str, scene_id: str, ext: str = 'jpg') -> Path:
    """Canonical image output path for a scene. Predictable so renderer/UI agree."""
    sd = _scenes_dir(project)
    sd.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r'[^A-Za-z0-9_\-]', '_', scene_id)[:60] or 'scene'
    # find index for filename ordering
    m = load_manifest(project)
    idx = 0
    for i, s in enumerate(m.get('scenes', []) if m else []):
        if s.get('scene_id') == scene_id:
            idx = s.get('index', i)
            break
    return sd / f'scene_{idx + 1:02d}_{safe_id}.{ext}'


def scene_motion_clip_path(project: str, scene_id: str) -> Path:
    sd = _scenes_dir(project)
    sd.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r'[^A-Za-z0-9_\-]', '_', scene_id)[:60] or 'scene'
    m = load_manifest(project)
    idx = 0
    for i, s in enumerate(m.get('scenes', []) if m else []):
        if s.get('scene_id') == scene_id:
            idx = s.get('index', i)
            break
    return sd / f'scene_{idx + 1:02d}_{safe_id}.mp4'


# ───────────────────────────────────────────────────────────────────────
# Regenerate one scene — image only (motion clip optional + slower)
# ───────────────────────────────────────────────────────────────────────

def regenerate_scene_image(project: str, scene_id: str,
                           prompt_override: Optional[str] = None,
                           width: int = 1920, height: int = 1080) -> dict:
    """Re-run image gen for ONE scene with current (or overridden) image_prompt.

    Updates the manifest with new image_path + image_provider + version bump.
    Returns {ok, scene_id, path, provider, elapsed_s} or {ok: False, error}.
    """
    t0 = time.time()
    scene = get_scene(project, scene_id)
    if not scene:
        return {'ok': False, 'error': f'scene not found: {scene_id}'}
    prompt = (prompt_override or scene.get('image_prompt') or '').strip()
    if not prompt:
        return {'ok': False, 'error': 'no image_prompt set on scene; supply prompt_override'}

    # Persist the override into the manifest BEFORE running so user edits stick
    if prompt_override:
        update_scene(project, scene_id, {'image_prompt': prompt}, bump_version=False)

    out_path = scene_image_path(project, scene_id)
    try:
        from .step_image_gen import fetch_with_fallback
        result = fetch_with_fallback(prompt, str(out_path), scene_id=scene.get('index', 0))
    except Exception as e:
        return {'ok': False, 'error': f'image gen import/run: {type(e).__name__}: {e}'}

    if not result.get('ok'):
        return {'ok': False, 'error': 'all providers failed', 'attempts': result.get('attempts', [])}

    # Update manifest with new path + provider + bump version
    update_scene(project, scene_id, {
        'image_path':     str(out_path.relative_to(PROJECT_ROOT)),
        'image_provider': result.get('provider'),
    }, bump_version=True)

    return {
        'ok':       True,
        'scene_id': scene_id,
        'path':     str(out_path.relative_to(PROJECT_ROOT)),
        'provider': result.get('provider'),
        'elapsed_s': round(time.time() - t0, 1),
        'prompt_used': prompt[:200],
    }


def regenerate_scene_motion_clip(project: str, scene_id: str,
                                  prompt_override: Optional[str] = None,
                                  duration_s: Optional[float] = None) -> dict:
    """Re-run image→video for ONE scene. Requires existing image_path.

    Updates manifest with motion_clip_path + motion_provider + version bump.
    """
    t0 = time.time()
    scene = get_scene(project, scene_id)
    if not scene:
        return {'ok': False, 'error': f'scene not found: {scene_id}'}
    image_path = scene.get('image_path')
    if not image_path:
        return {'ok': False, 'error': 'scene has no image_path; run regenerate_scene_image first'}
    abs_image = (PROJECT_ROOT / image_path) if not os.path.isabs(image_path) else Path(image_path)
    if not abs_image.exists():
        return {'ok': False, 'error': f'image file missing: {abs_image}'}

    prompt = (prompt_override
              or scene.get('motion_prompt')
              or scene.get('narration_excerpt')
              or 'subtle cinematic motion, slow zoom, ambient atmosphere')
    if prompt_override:
        update_scene(project, scene_id, {'motion_prompt': prompt}, bump_version=False)

    out_path = scene_motion_clip_path(project, scene_id)
    dur = duration_s if duration_s is not None else float(scene.get('duration_seconds') or 5.0)
    try:
        from .step_image_gen import fetch_video_with_fallback
        result = fetch_video_with_fallback(str(abs_image), prompt, str(out_path),
                                           duration_s=dur,
                                           scene_id=scene.get('index', 0))
    except Exception as e:
        return {'ok': False, 'error': f'video gen import/run: {type(e).__name__}: {e}'}

    if not result.get('ok'):
        return {
            'ok':       False,
            'skipped':  bool(result.get('skipped')),
            'error':    'no video provider succeeded',
            'attempts': result.get('attempts', []),
        }

    update_scene(project, scene_id, {
        'motion_clip_path': str(out_path.relative_to(PROJECT_ROOT)),
        'motion_provider':  result.get('provider'),
    }, bump_version=True)

    return {
        'ok':        True,
        'scene_id':  scene_id,
        'path':      str(out_path.relative_to(PROJECT_ROOT)),
        'provider':  result.get('provider'),
        'elapsed_s': round(time.time() - t0, 1),
    }


# ───────────────────────────────────────────────────────────────────────
# Project listing — for the dashboard project picker
# ───────────────────────────────────────────────────────────────────────

def list_projects_with_manifests() -> list[dict]:
    """Inventory of all projects that have a saved scene manifest."""
    base = PROJECT_ROOT / 'out' / 'projects'
    if not base.exists():
        return []
    out: list[dict] = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        mp = d / 'scene_manifest.json'
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text(encoding='utf-8'))
            out.append({
                'project':       d.name,
                'scene_count':   m.get('scene_count', 0),
                'subject':       m.get('subject', ''),
                'length_seconds': m.get('length_seconds', 0),
                'saved_at':      m.get('saved_at', ''),
            })
        except Exception:
            pass
    return out
