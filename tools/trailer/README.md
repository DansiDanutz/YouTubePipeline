# Trailer Builder

30-second cinematic trailer pipeline. Cloud Flux DEV for hero scenes + ffmpeg Ken Burns motion + macOS `say` narration + ffmpeg assembly. Verified live 2026-05-11 — produces 1920x1080 H.264+AAC MP4 in ~3 minutes wall-clock.

## Requirements

- `security find-generic-password -s COMFY_CLOUD_API_KEY` returns the Cloud key (M11 wired this)
- macOS `say` (built-in)
- `ffmpeg` 8.x with libx264 + AAC

## Usage

```bash
# Default: builds DansLab trailer
tools/trailer/build_trailer.sh

# Custom scenes + output dir
tools/trailer/build_trailer.sh path/to/scenes.json path/to/output_dir
```

## scenes.json schema

```json
[
  {
    "id": 1,
    "duration": 5,                   // seconds
    "motion": "zoom_in",             // zoom_in | zoom_out | pan_right | pan_left | zoom_in_slow
    "prompt": "Cinematic ..."        // Flux DEV prompt
  }
]
```

Total trailer length = sum of `duration` across all scenes.

## Customizing narration

Set the env var before running:

```bash
TRAILER_NARRATION="Your custom 30-second narration here..." \
SAY_VOICE=Tom SAY_RATE=170 \
tools/trailer/build_trailer.sh
```

## Output

- `<out>/scene_N.png` — 1920x1080 hero images
- `<out>/clip_N.mp4` — Ken Burns motion clips
- `<out>/narration.aac` — AAC voice-over
- `<out>/trailer.mp4` — **final 30s deliverable**

## Cost

~6 Cloud Flux DEV credits per trailer. Local rendering would OOM on Mac M-series for Flux DEV (use Flux schnell locally for drafts; Cloud for production).
