#!/usr/bin/env python3.13
"""MCP server availability snapshot for the dashboard provider bar.

Reads ~/.claude.json (Claude Code config) to see which MCP servers are
registered, then surfaces the ones the pipeline cares about (shadcn-ui MCP for
Step 3 component lookups, others for future use). Read-only — never mutates
the config.
"""
from __future__ import annotations

import json
from pathlib import Path

CLAUDE_CONFIG = Path.home() / '.claude.json'

# Pipeline-relevant MCP servers we want to know about
WATCH_MCPS = ('shadcn-ui', 'shadcn', 'figma', 'higgsfield', 'siegfried')


def _load_claude_config() -> dict:
    if not CLAUDE_CONFIG.exists():
        return {}
    try:
        return json.loads(CLAUDE_CONFIG.read_text(encoding='utf-8'))
    except Exception:
        return {}


def registered_mcps() -> dict[str, dict]:
    """Returns {name: {command, status_hint}} for every user-scoped MCP."""
    cfg = _load_claude_config()
    # The structure varies; user-scope MCPs live under "mcpServers" at the top
    servers = cfg.get('mcpServers') or {}
    out = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        out[name] = {
            'command': entry.get('command', ''),
            'args':    entry.get('args', []),
            'type':    entry.get('type', 'stdio'),
        }
    return out


def status() -> dict:
    """Pipeline-relevant subset of registered MCPs, plus a handful of known-name checks."""
    all_mcps = registered_mcps()
    watched = {}
    for name in WATCH_MCPS:
        # Match exact or prefix
        match = None
        for registered_name in all_mcps:
            if registered_name == name or registered_name.startswith(name):
                match = registered_name
                break
        watched[name] = {
            'available':    match is not None,
            'registered_as': match,
        }
    return {
        'total_registered': len(all_mcps),
        'all_names':        sorted(all_mcps.keys()),
        'watched':          watched,
    }
