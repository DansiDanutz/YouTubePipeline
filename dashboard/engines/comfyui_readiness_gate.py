#!/usr/bin/env python3
"""ComfyUI / Wan 2.1 readiness gate.

This is a non-generating integration check for the future real I2V lane.
It never starts ComfyUI, downloads models, or generates media. It only reports
whether the existing local pipeline has the pieces needed before Step 4 can use
Wan 2.1 instead of the current FFmpeg procedural fallback.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_WORKFLOWS = [
    "comfyui-workflows/flux-base-frames.json",
    "comfyui-workflows/wan21-img2vid.json",
]
MODEL_HINTS = [
    "wan2.1",
    "wan_2.1",
    "umt5",
    "clip_vision",
    "vae",
]


def probe_endpoint(url: str) -> dict:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/system_stats", timeout=2) as r:
            body = r.read(2048).decode("utf-8", errors="replace")
            return {"ok": True, "status": r.status, "sample": body[:300]}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "message": str(e)[:300]}


def scan_models(root: Path) -> dict:
    candidates = []
    for base in [root / "ComfyUI" / "models", root / "comfyui" / "models", Path.home() / "ComfyUI" / "models"]:
        if base.exists():
            for p in base.rglob("*"):
                if p.is_file() and p.suffix.lower() in {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}:
                    rel = str(p)
                    low = rel.lower()
                    if any(h in low for h in MODEL_HINTS):
                        candidates.append({"path": rel, "bytes": p.stat().st_size})
    return {"count": len(candidates), "candidates": candidates[:50]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--endpoint", default="http://127.0.0.1:8188")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    workflow_status = []
    for rel in REQUIRED_WORKFLOWS:
        p = root / rel
        workflow_status.append({"path": str(p), "exists": p.exists(), "bytes": p.stat().st_size if p.exists() else 0})
    endpoint = probe_endpoint(args.endpoint)
    models = scan_models(root)
    hard_fails = []
    if not all(w["exists"] for w in workflow_status):
        hard_fails.append("missing_comfyui_workflow")
    if not endpoint["ok"]:
        hard_fails.append("comfyui_endpoint_not_running")
    if models["count"] == 0:
        hard_fails.append("wan_or_clip_models_not_found")
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "passed": not hard_fails,
        "hard_fails": hard_fails,
        "endpoint": endpoint,
        "workflows": workflow_status,
        "models": models,
        "decision": "Use Wan/ComfyUI I2V only if this gate passes. Current safe fallback remains FFmpeg procedural motion.",
    }
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    print(text)
    return 0 if result["passed"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
