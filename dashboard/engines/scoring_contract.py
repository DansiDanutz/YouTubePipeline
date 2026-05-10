#!/usr/bin/env python3
"""Strict scoring contract and stop/go gates for Zmarty Video Pipeline.

This module is intentionally deterministic for technical gates. Creative scoring
can be layered later, but generated assets cannot advance without this contract:
- prompt saved
- score JSON saved
- technical validation passes
- total score >= threshold
- retry loop stops on failure, never silently advances
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

TARGET_W = 1920
TARGET_H = 1080
TARGET_FPS = 30
IMAGE_THRESHOLD = 85
CLIP_THRESHOLD = 85
FINAL_THRESHOLD = 90
VOICE_TECH_THRESHOLD = 100
VOICE_QUALITY_THRESHOLD = 85
DEFAULT_MAX_RETRIES = 3

SCORING_WEIGHTS = {
    "script_story_relevance": 20,
    "prompt_match": 20,
    "visual_quality": 20,
    "scene_continuity": 15,
    "animation_motion_quality": 15,
    "technical_validity": 10,
}

HARD_FAILS = [
    "missing_asset",
    "empty_asset",
    "prompt_not_saved",
    "score_json_not_saved",
    "ffprobe_failure",
    "decode_failure",
    "wrong_resolution",
    "wrong_fps",
    "missing_audio_stream",
    "wrong_or_missing_requested_voice",
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def ffprobe(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    cmd = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(p),
    ]
    out = subprocess.check_output(cmd, text=True, timeout=30)
    return json.loads(out)


def decode_check(path: str | Path) -> tuple[bool, str]:
    p = Path(path)
    cmd = ["ffmpeg", "-v", "error", "-i", str(p), "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "decode failed")[:1000]


def image_metadata(path: str | Path) -> dict[str, Any]:
    data = ffprobe(path)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("no video/image stream")
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec": video.get("codec_name"),
        "duration": video.get("duration") or data.get("format", {}).get("duration"),
        "format": data.get("format", {}).get("format_name"),
        "size": int(data.get("format", {}).get("size") or Path(path).stat().st_size),
    }


def video_metadata(path: str | Path) -> dict[str, Any]:
    data = ffprobe(path)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("no video stream")
    fps_raw = video.get("r_frame_rate") or "0/1"
    try:
        a, b = fps_raw.split("/")
        fps = float(a) / float(b) if float(b) else 0
    except Exception:
        fps = 0
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "fps_raw": fps_raw,
        "codec": video.get("codec_name"),
        "duration": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "size": int(data.get("format", {}).get("size") or Path(path).stat().st_size),
    }


def prompt_sidecar_path(asset_path: str | Path) -> Path:
    p = Path(asset_path)
    return p.with_suffix(p.suffix + ".prompt.txt")


def score_sidecar_path(asset_path: str | Path) -> Path:
    p = Path(asset_path)
    return p.with_suffix(p.suffix + ".score.json")


def save_prompt(asset_path: str | Path, prompt: str) -> Path:
    sidecar = prompt_sidecar_path(asset_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(prompt or "", encoding="utf-8")
    return sidecar


def score_image_asset(
    asset_path: str | Path,
    *,
    scene_id: str,
    prompt: str,
    provider: str = "unknown",
    seed: int | None = None,
    retry_index: int = 0,
    threshold: int = IMAGE_THRESHOLD,
    script_story_relevance: int = 20,
    prompt_match: int = 20,
    visual_quality: int = 20,
    scene_continuity: int = 15,
) -> dict[str, Any]:
    """Score one image and save JSON. Hard fails override numeric score."""
    path = Path(asset_path)
    prompt_file = prompt_sidecar_path(path)
    score_file = score_sidecar_path(path)
    hard_fail_reasons: list[str] = []
    metadata: dict[str, Any] = {}

    if not path.exists():
        hard_fail_reasons.append("missing_asset")
    elif path.stat().st_size <= 0:
        hard_fail_reasons.append("empty_asset")

    if not prompt_file.exists() or not prompt_file.read_text(errors="ignore").strip():
        hard_fail_reasons.append("prompt_not_saved")

    if path.exists() and path.stat().st_size > 0:
        try:
            metadata = image_metadata(path)
            if metadata.get("width") != TARGET_W or metadata.get("height") != TARGET_H:
                hard_fail_reasons.append("wrong_resolution")
        except Exception as exc:
            metadata = {"error": str(exc)}
            hard_fail_reasons.append("ffprobe_failure")
        ok_decode, decode_error = decode_check(path)
        if not ok_decode:
            metadata["decode_error"] = decode_error
            hard_fail_reasons.append("decode_failure")

    technical_validity = SCORING_WEIGHTS["technical_validity"] if not hard_fail_reasons else 0
    scores = {
        "script_story_relevance": max(0, min(SCORING_WEIGHTS["script_story_relevance"], script_story_relevance)),
        "prompt_match": max(0, min(SCORING_WEIGHTS["prompt_match"], prompt_match)),
        "visual_quality": max(0, min(SCORING_WEIGHTS["visual_quality"], visual_quality)),
        "scene_continuity": max(0, min(SCORING_WEIGHTS["scene_continuity"], scene_continuity)),
        "animation_motion_quality": 0,
        "technical_validity": technical_validity,
    }
    total = sum(scores.values())
    passed = total >= threshold and not hard_fail_reasons
    record = {
        "schema_version": "1.0",
        "created_at": now_iso(),
        "asset_type": "image",
        "scene_id": str(scene_id),
        "path": str(path),
        "prompt_path": str(prompt_file),
        "prompt": prompt,
        "provider": provider,
        "seed": seed,
        "retry_index": retry_index,
        "threshold": threshold,
        "weights": SCORING_WEIGHTS,
        "scores": scores,
        "total_score": total,
        "passed": passed,
        "hard_fail_reasons": hard_fail_reasons,
        "metadata": metadata,
        "stop_go": "GO" if passed else "STOP_RETRY_SAME_STEP",
    }
    score_file.parent.mkdir(parents=True, exist_ok=True)
    score_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
    # Verify the score JSON exists after writing. If not, rewrite record as failed.
    if not score_file.exists() or score_file.stat().st_size <= 0:
        record["hard_fail_reasons"].append("score_json_not_saved")
        record["passed"] = False
        record["stop_go"] = "STOP_RETRY_SAME_STEP"
    return record


def run_retry_loop(
    *,
    scene_id: str,
    prompt: str,
    asset_path_factory: Callable[[int], str | Path],
    generate_once: Callable[[int, str | Path], dict[str, Any]],
    max_retries: int = DEFAULT_MAX_RETRIES,
    threshold: int = IMAGE_THRESHOLD,
) -> dict[str, Any]:
    """Generate/score loop. Returns only after pass or explicit failure.

    generate_once(retry_index, asset_path) must create the file or return ok=False.
    """
    attempts = []
    for retry_index in range(max_retries + 1):
        asset_path = Path(asset_path_factory(retry_index))
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        save_prompt(asset_path, prompt)
        gen = generate_once(retry_index, asset_path) or {}
        record = score_image_asset(
            asset_path,
            scene_id=scene_id,
            prompt=prompt,
            provider=gen.get("provider", "unknown"),
            seed=gen.get("seed"),
            retry_index=retry_index,
            threshold=threshold,
        )
        record["generation"] = {k: v for k, v in gen.items() if k not in {"secret", "api_key", "token"}}
        score_sidecar_path(asset_path).write_text(json.dumps(record, indent=2), encoding="utf-8")
        attempts.append(record)
        if record["passed"]:
            return {"passed": True, "scene_id": scene_id, "attempts": attempts, "final": record}
    return {"passed": False, "scene_id": scene_id, "attempts": attempts, "final": attempts[-1] if attempts else None}


def score_video_clip(
    asset_path: str | Path,
    *,
    scene_id: str,
    motion_prompt: str,
    retry_index: int = 0,
    threshold: int = CLIP_THRESHOLD,
    expected_fps: int = TARGET_FPS,
    min_duration_s: float = 2.0,
) -> dict[str, Any]:
    """Score one animated clip and save JSON sidecar."""
    path = Path(asset_path)
    prompt_file = prompt_sidecar_path(path)
    score_file = score_sidecar_path(path)
    hard_fail_reasons: list[str] = []
    metadata: dict[str, Any] = {}
    if not path.exists():
        hard_fail_reasons.append("missing_asset")
    elif path.stat().st_size <= 0:
        hard_fail_reasons.append("empty_asset")
    if not prompt_file.exists() or not prompt_file.read_text(errors="ignore").strip():
        hard_fail_reasons.append("prompt_not_saved")
    if path.exists() and path.stat().st_size > 0:
        try:
            metadata = video_metadata(path)
            if metadata.get("width") != TARGET_W or metadata.get("height") != TARGET_H:
                hard_fail_reasons.append("wrong_resolution")
            if round(float(metadata.get("fps") or 0)) != expected_fps:
                hard_fail_reasons.append("wrong_fps")
            if float(metadata.get("duration") or 0) < min_duration_s:
                hard_fail_reasons.append("decode_failure")
        except Exception as exc:
            metadata = {"error": str(exc)}
            hard_fail_reasons.append("ffprobe_failure")
        ok_decode, decode_error = decode_check(path)
        if not ok_decode:
            metadata["decode_error"] = decode_error
            hard_fail_reasons.append("decode_failure")
    technical_validity = SCORING_WEIGHTS["technical_validity"] if not hard_fail_reasons else 0
    scores = {
        "script_story_relevance": 20,
        "prompt_match": 20,
        "visual_quality": 20,
        "scene_continuity": 15,
        "animation_motion_quality": 15 if not hard_fail_reasons else 0,
        "technical_validity": technical_validity,
    }
    total = sum(scores.values())
    passed = total >= threshold and not hard_fail_reasons
    record = {
        "schema_version": "1.0",
        "created_at": now_iso(),
        "asset_type": "clip",
        "scene_id": str(scene_id),
        "path": str(path),
        "prompt_path": str(prompt_file),
        "motion_prompt": motion_prompt,
        "retry_index": retry_index,
        "threshold": threshold,
        "weights": SCORING_WEIGHTS,
        "scores": scores,
        "total_score": total,
        "passed": passed,
        "hard_fail_reasons": hard_fail_reasons,
        "metadata": metadata,
        "stop_go": "GO" if passed else "STOP_RETRY_SAME_STEP",
    }
    score_file.parent.mkdir(parents=True, exist_ok=True)
    score_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def contract_self_test(work_dir: str | Path) -> dict[str, Any]:
    """Validate Step 1 contract without generating real media."""
    work = Path(work_dir)
    proof_dir = work / "proof" / "scoring_contract"
    proof_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "created_at": now_iso(),
        "contract": {
            "target_width": TARGET_W,
            "target_height": TARGET_H,
            "target_fps": TARGET_FPS,
            "image_threshold": IMAGE_THRESHOLD,
            "clip_threshold": CLIP_THRESHOLD,
            "final_threshold": FINAL_THRESHOLD,
            "default_max_retries": DEFAULT_MAX_RETRIES,
            "hard_fails": HARD_FAILS,
            "weights": SCORING_WEIGHTS,
        },
        "checks": [],
    }
    # Known missing file must fail and save score JSON.
    missing = proof_dir / "missing_scene.jpg"
    prompt = "contract self-test prompt"
    save_prompt(missing, prompt)
    rec = score_image_asset(missing, scene_id="selftest_missing", prompt=prompt)
    result["checks"].append({
        "name": "missing image hard-fails",
        "passed": (not rec["passed"] and "missing_asset" in rec["hard_fail_reasons"] and score_sidecar_path(missing).exists()),
        "score_path": str(score_sidecar_path(missing)),
    })
    result["passed"] = all(c["passed"] for c in result["checks"])
    out = proof_dir / "STEP1_SCORING_CONTRACT_PROOF.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["proof_path"] = str(out)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="run Step 1 contract self-test")
    ap.add_argument("--work-dir", default=".")
    args = ap.parse_args()
    if args.self_test:
        result = contract_self_test(args.work_dir)
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed") else 1
    ap.error("nothing to do; use --self-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
