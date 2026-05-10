#!/usr/bin/env python3
"""Enhanced no-reference final-video QA for the gated video pipeline.

This is the first open-source improvement integrated after the baseline passed.
It uses local FFmpeg/FFprobe only:
- ffprobe stream/container validation
- blackdetect for black-screen intervals
- freezedetect for stuck frames
- silencedetect for silent-audio intervals
- local filter capability detection for libvmaf/ssim/psnr future use

It does not replace the creative gate. It adds objective stop/go evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TARGET_W = 1920
TARGET_H = 1080
TARGET_FPS = "30/1"
MIN_DURATION = 39.0
MAX_DURATION = 43.0
MAX_BLACK_SECONDS = 0.75
MAX_FREEZE_SECONDS = 1.25
MAX_SILENCE_SECONDS = 1.75


def run(cmd: list[str], *, timeout: int = 120, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=check)


def ffprobe(path: Path) -> dict[str, Any]:
    cp = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], check=True)
    return json.loads(cp.stdout)


def available_filters() -> dict[str, bool]:
    cp = run(["ffmpeg", "-hide_banner", "-filters"], timeout=60)
    text = cp.stdout + cp.stderr
    names = ["libvmaf", "ssim", "psnr", "blackdetect", "freezedetect", "silencedetect"]
    return {name: bool(re.search(rf"\b{name}\b", text)) for name in names}


def parse_intervals(stderr: str, kind: str) -> list[dict[str, float]]:
    intervals = []
    # blackdetect: black_start:0 black_end:1.2 black_duration:1.2
    # freezedetect: freeze_start:0 freeze_duration:2 freeze_end:2
    # silencedetect: silence_start:0 / silence_end:1 | silence_duration:1
    if kind == "black":
        pattern = re.compile(r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)")
    elif kind == "freeze":
        pattern = re.compile(r"freeze_start:(?P<start>[0-9.]+).*?freeze_duration:(?P<duration>[0-9.]+).*?freeze_end:(?P<end>[0-9.]+)")
    else:
        pattern = re.compile(r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<duration>[0-9.]+)")
    for m in pattern.finditer(stderr.replace("\n", " ")):
        d = {k: float(v) for k, v in m.groupdict(default="0").items()}
        if kind == "silence":
            d["start"] = max(0.0, d["end"] - d["duration"])
        intervals.append(d)
    return intervals


def run_detector(path: Path, detector: str) -> dict[str, Any]:
    if detector == "black":
        vf = "blackdetect=d=0.20:pix_th=0.10"
        kind = "black"
    elif detector == "freeze":
        vf = "freezedetect=n=-60dB:d=0.75"
        kind = "freeze"
    elif detector == "silence":
        # audio filter, not video filter
        cp = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "silencedetect=n=-45dB:d=0.50", "-f", "null", "-"], timeout=180)
        intervals = parse_intervals(cp.stderr, "silence")
        return {"returncode": cp.returncode, "intervals": intervals, "total_duration": sum(x.get("duration", 0) for x in intervals)}
    else:
        raise ValueError(detector)

    cp = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf", vf, "-an", "-f", "null", "-"], timeout=180)
    intervals = parse_intervals(cp.stderr, kind)
    return {"returncode": cp.returncode, "intervals": intervals, "total_duration": sum(x.get("duration", 0) for x in intervals)}


def qa(path: Path) -> dict[str, Any]:
    hard_fails: list[str] = []
    if not path.exists() or path.stat().st_size <= 0:
        hard_fails.append("missing_or_empty_video")
        return {"passed": False, "hard_fails": hard_fails, "path": str(path)}

    probe = ffprobe(path)
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0)

    if not video:
        hard_fails.append("missing_video_stream")
    else:
        if video.get("width") != TARGET_W or video.get("height") != TARGET_H:
            hard_fails.append("wrong_resolution")
        if video.get("r_frame_rate") != TARGET_FPS:
            hard_fails.append("wrong_fps")
    if not audio:
        hard_fails.append("missing_audio_stream")
    if not (MIN_DURATION <= duration <= MAX_DURATION):
        hard_fails.append("duration_out_of_range")

    filters = available_filters()
    black = run_detector(path, "black") if filters.get("blackdetect") else {"skipped": True}
    freeze = run_detector(path, "freeze") if filters.get("freezedetect") else {"skipped": True}
    silence = run_detector(path, "silence") if filters.get("silencedetect") else {"skipped": True}

    if black.get("total_duration", 0) > MAX_BLACK_SECONDS:
        hard_fails.append("excess_black_frames")
    if freeze.get("total_duration", 0) > MAX_FREEZE_SECONDS:
        hard_fails.append("excess_frozen_frames")
    if silence.get("total_duration", 0) > MAX_SILENCE_SECONDS:
        hard_fails.append("excess_audio_silence")

    score = 100
    score -= min(20, int(black.get("total_duration", 0) * 10))
    score -= min(20, int(freeze.get("total_duration", 0) * 10))
    score -= min(15, int(silence.get("total_duration", 0) * 5))
    score -= 40 if hard_fails else 0
    score = max(0, score)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "path": str(path),
        "passed": not hard_fails and score >= 90,
        "score": score,
        "threshold": 90,
        "hard_fails": hard_fails,
        "container": {"duration": duration, "size": int(fmt.get("size") or 0), "format": fmt.get("format_name")},
        "video": {"codec": video.get("codec_name") if video else None, "width": video.get("width") if video else None, "height": video.get("height") if video else None, "fps": video.get("r_frame_rate") if video else None},
        "audio": {"codec": audio.get("codec_name") if audio else None, "sample_rate": audio.get("sample_rate") if audio else None, "channels": audio.get("channels") if audio else None},
        "open_source_filters": filters,
        "detectors": {"blackdetect": black, "freezedetect": freeze, "silencedetect": silence},
        "future_reference_metrics_available": {"libvmaf": filters.get("libvmaf"), "ssim": filters.get("ssim"), "psnr": filters.get("psnr")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    result = qa(Path(args.video))
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    print(text)
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
