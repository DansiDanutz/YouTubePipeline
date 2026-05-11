# Trailer Builder

30-second cinematic trailer pipeline. Cloud Flux DEV for hero scenes + ElevenLabs Brian narration (with macOS `say` fallback) + ffmpeg Ken Burns motion + ffmpeg assembly. Verified live 2026-05-11 — produces 1920x1080 H.264+AAC MP4 with premium voiceover in ~3 minutes wall-clock.

## Requirements

- `security find-generic-password -s COMFY_CLOUD_API_KEY` returns the Cloud key (M11 wired this)
- `security find-generic-password -s ELEVENLABS_API_KEY` returns the ElevenLabs key (recommended for production-quality voiceover)
- macOS `say` (built-in fallback when ElevenLabs key missing)
- `ffmpeg` 8.x with libx264 + AAC

## Usage

```bash
# Default: builds DansLab trailer with ElevenLabs Brian voice
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

Set env vars before running:

```bash
# Custom script (works for both ElevenLabs and 'say')
TRAILER_NARRATION="Your custom 30-second narration here..." \
  tools/trailer/build_trailer.sh

# Force a different ElevenLabs voice (default: Brian = nPczCjzI2devNBz1zQrb)
ELEVENLABS_VOICE_BRIAN=<voice_id> \
  tools/trailer/build_trailer.sh

# Force a different ElevenLabs model (default: eleven_flash_v2_5 — fastest, cheapest)
# Alternatives: eleven_multilingual_v2 (best quality, more credits)
ELEVENLABS_MODEL_ID=eleven_multilingual_v2 \
  tools/trailer/build_trailer.sh

# Force macOS 'say' fallback even when ElevenLabs key present
ELEVENLABS_API_KEY="" SAY_VOICE=Tom SAY_RATE=170 \
  tools/trailer/build_trailer.sh
```

## Voice routing priority

1. **ElevenLabs Brian** (Flash v2.5) — when `ELEVENLABS_API_KEY` resolvable
2. **macOS `say` Alex** — fallback (basic but always works)

When neither works, the build halts. Both auto-detected; no manual gating.

## Output

- `<out>/scene_N.png` — 1920x1080 hero images
- `<out>/clip_N.mp4` — Ken Burns motion clips
- `<out>/narration.mp3` — ElevenLabs Brian (when active)
- `<out>/narration.aac` — final AAC voiceover (re-encoded from MP3 or AIFF)
- `<out>/trailer.mp4` — **final 30s deliverable**

## Music bed (Cloud ACE-Step, default-on)

The script auto-generates a cinematic ambient music bed via Cloud ACE-Step (`ace_step_v1_3.5b.safetensors`) and ducks it under the narration. Skip with `TRAILER_NO_MUSIC=1`. Override the music tags / negative tags / duck volume:

```bash
TRAILER_MUSIC_TAGS="dark cinematic score, tense build, orchestral hits, 110 bpm, instrumental" \
TRAILER_MUSIC_NEG_TAGS="vocals, lyrics, harsh distortion" \
TRAILER_MUSIC_VOLUME=0.22 \
  tools/trailer/build_trailer.sh
```

The bed is trimmed to the trailer length, fades in 1s, fades out the last 2s, and mixes at ~18% volume so the narration stays primary.

## Cost

| Component | Cost |
|---|---|
| Cloud Flux DEV (6 hero scenes) | ~6 Cloud credits per trailer |
| Cloud ACE-Step (~32s music) | ~3 Cloud credits per trailer |
| ElevenLabs Brian Flash v2.5 (~80 words narration) | ~500 ElevenLabs credits per trailer |
| Ken Burns motion + concat + mux (ffmpeg) | free (local) |

Local rendering would OOM on Mac M-series for Flux DEV (use Flux schnell locally for drafts; Cloud for production). ElevenLabs Flash v2.5 is ~10x cheaper than Multilingual v2 with negligible quality loss for short trailers.
