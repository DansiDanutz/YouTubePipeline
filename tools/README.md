# tools/ — external runtime dependencies

These tools are required at runtime but **not** committed (size). Install them locally before running the pipeline.

## whisper.cpp (Step 6 — subtitles)

```bash
cd tools
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
make -j

# download a model (base.en is enough for English)
bash ./models/download-ggml-model.sh base.en
```

The pipeline expects `tools/whisper/main` (or `main.exe` on Windows) plus `tools/whisper/models/*.bin`. Override with `WHISPER_BIN` and `WHISPER_MODEL_DIR` env vars.

## Piper TTS (Step 5 — local fallback when ElevenLabs unconfigured)

Download binary + voice from [rhasspy/piper releases](https://github.com/rhasspy/piper/releases):

```bash
cd tools
mkdir piper && cd piper

# choose your platform
curl -LO https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip
unzip piper_windows_amd64.zip

# download a voice
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

The pipeline expects `tools/piper/piper.exe` (Windows) or `tools/piper/piper` (Mac/Linux) plus an `*.onnx` voice file.

## Why these aren't committed

- **whisper.cpp** is ~300MB after build (.dylib, .so, .o, model weights)
- **Piper** ships per-platform binaries (~50MB each)

Keeping them out of git keeps clones fast (~5MB instead of 350MB+) and avoids LFS billing.

## ElevenLabs (Step 5 — primary TTS)

ElevenLabs is the **preferred** voice for narration. Set `ELEVENLABS_API_KEY` in `~/.openclaw/fleet.env` and the engine routes through them automatically. Piper is the local fallback when the key is unset.
