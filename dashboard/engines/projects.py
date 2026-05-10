#!/usr/bin/env python3.13
"""Projects — multi-video workspace abstraction.

Each project is a separate video the user is producing. Projects have:
  - id (slug + short hash)
  - name (human-readable)
  - topic_hint (optional one-liner that pre-fills Step 1)
  - created_at / updated_at
  - state — the full 9-step pipeline state (resolved flags, outputs, ratings)

Storage: ~/.openclaw/zmarty_projects/<project_id>/
  project.json   — metadata
  state.json     — pipeline state (steps, results, etc.)

Active project tracked at ~/.openclaw/zmarty_projects/.active (single id).
The frontend reads the active project on load and namespaces all operations.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / '.openclaw' / 'zmarty_projects'
ACTIVE_FILE = PROJECTS_DIR / '.active'
PROJECT_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,79}$')


def _ensure_dir() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _project_dir(project_id: str) -> Path | None:
    project_id = (project_id or '').strip()
    if not PROJECT_ID_RE.fullmatch(project_id):
        return None
    root = PROJECTS_DIR.resolve()
    path = (root / project_id).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return s[:32] or 'project'


def _project_id(name: str) -> str:
    """Stable id = slug + 6-char hash of (name + timestamp) so renames don't collide."""
    slug = _slug(name)
    h = hashlib.sha256(f'{name}{time.time()}'.encode()).hexdigest()[:6]
    return f'{slug}-{h}'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_projects() -> list[dict]:
    """Return all projects sorted by updated_at (most recent first)."""
    _ensure_dir()
    projects: list[dict] = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir() or d.name.startswith('.'):
            continue
        meta = d / 'project.json'
        if not meta.exists():
            continue
        try:
            projects.append(json.loads(meta.read_text(encoding='utf-8')))
        except Exception:
            continue
    projects.sort(key=lambda p: p.get('updated_at', ''), reverse=True)
    return projects


def get_active_id() -> str:
    """Return the currently-active project id, or empty string if none set."""
    _ensure_dir()
    if not ACTIVE_FILE.exists():
        return ''
    try:
        return ACTIVE_FILE.read_text(encoding='utf-8').strip()
    except Exception:
        return ''


def set_active_id(project_id: str) -> dict:
    _ensure_dir()
    proj_dir = _project_dir(project_id)
    if proj_dir is None:
        return {'ok': False, 'error': 'Invalid project id'}
    if not proj_dir.exists():
        return {'ok': False, 'error': f'Project not found: {project_id}'}
    ACTIVE_FILE.write_text(project_id, encoding='utf-8')
    return {'ok': True, 'active_id': project_id}


def create_project(name: str, topic_hint: str = '') -> dict:
    if not (name or '').strip():
        return {'ok': False, 'error': 'Project name is required'}
    _ensure_dir()
    pid = _project_id(name.strip())
    proj_dir = PROJECTS_DIR / pid
    proj_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        'id': pid,
        'name': name.strip(),
        'topic_hint': (topic_hint or '').strip(),
        'created_at': _now(),
        'updated_at': _now(),
    }
    (proj_dir / 'project.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (proj_dir / 'state.json').write_text(json.dumps({'steps': []}, indent=2), encoding='utf-8')
    set_active_id(pid)
    return {'ok': True, 'project': meta}


def get_project(project_id: str) -> dict:
    _ensure_dir()
    proj_dir = _project_dir(project_id)
    if proj_dir is None:
        return {'ok': False, 'error': 'Invalid project id'}
    if not proj_dir.exists():
        return {'ok': False, 'error': 'Project not found'}
    meta_file = proj_dir / 'project.json'
    state_file = proj_dir / 'state.json'
    try:
        meta = json.loads(meta_file.read_text(encoding='utf-8')) if meta_file.exists() else {}
        state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
    except Exception as e:
        return {'ok': False, 'error': f'Read failed: {e}'}
    return {'ok': True, 'project': meta, 'state': state}


def update_project_state(project_id: str, state: dict) -> dict:
    _ensure_dir()
    proj_dir = _project_dir(project_id)
    if proj_dir is None:
        return {'ok': False, 'error': 'Invalid project id'}
    if not proj_dir.exists():
        return {'ok': False, 'error': 'Project not found'}
    try:
        # Update state
        (proj_dir / 'state.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
        # Bump updated_at on metadata
        meta_file = proj_dir / 'project.json'
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
            meta['updated_at'] = _now()
            meta_file.write_text(json.dumps(meta, indent=2), encoding='utf-8')
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def rename_project(project_id: str, new_name: str) -> dict:
    if not (new_name or '').strip():
        return {'ok': False, 'error': 'name is required'}
    _ensure_dir()
    proj_dir = _project_dir(project_id)
    if proj_dir is None:
        return {'ok': False, 'error': 'Invalid project id'}
    meta_file = proj_dir / 'project.json'
    if not meta_file.exists():
        return {'ok': False, 'error': 'Project not found'}
    try:
        meta = json.loads(meta_file.read_text(encoding='utf-8'))
        meta['name'] = new_name.strip()
        meta['updated_at'] = _now()
        meta_file.write_text(json.dumps(meta, indent=2), encoding='utf-8')
        return {'ok': True, 'project': meta}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def delete_project(project_id: str) -> dict:
    _ensure_dir()
    proj_dir = _project_dir(project_id)
    if proj_dir is None:
        return {'ok': False, 'error': 'Invalid project id'}
    if not proj_dir.exists():
        return {'ok': False, 'error': 'Project not found'}
    try:
        # Remove all files in the directory
        for f in proj_dir.iterdir():
            if not f.is_file():
                return {'ok': False, 'error': f'Project directory contains non-file entry: {f.name}'}
            f.unlink()
        proj_dir.rmdir()
        # Clear active if it was this one
        if get_active_id() == project_id:
            ACTIVE_FILE.unlink(missing_ok=True)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def projects_summary() -> dict:
    """Cross-project overview — total projects, total resolved steps,
    most-active project. Used by the dashboard header."""
    projects = list_projects()
    if not projects:
        return {'count': 0, 'active_id': get_active_id()}
    total_resolved = 0
    most_active = None
    for p in projects:
        try:
            state = json.loads((PROJECTS_DIR / p['id'] / 'state.json').read_text(encoding='utf-8'))
            resolved = sum(1 for s in (state.get('steps') or []) if s.get('resolved'))
            total_resolved += resolved
            if most_active is None or resolved > most_active.get('_resolved', 0):
                most_active = {**p, '_resolved': resolved}
        except Exception:
            continue
    return {
        'count': len(projects),
        'total_resolved_steps': total_resolved,
        'active_id': get_active_id(),
        'most_active': most_active,
    }
