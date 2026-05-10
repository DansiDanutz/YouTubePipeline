# OSS Stack

The canonical, license-checked, production-grade open-source stack. **Zero paid APIs in any default code path.** Updated 2026-05-10.

---

## License rule

We are building a paid-membership product. The license stack matters:

- ✅ **Apache 2.0 / MIT / BSD** — use freely, sell, modify, distribute.
- ⚠️ **GPL-3.0** (ComfyUI, Blender) — fine for internal tooling that generates output we sell. Don't redistribute the *source* of GPL tools as part of our product.
- ❌ **CC-BY-NC** (F5-TTS) — no commercial use. **Rejected.**
- ❌ **GSAP commercial** — needs paid license above team thresholds. Use **anime.js v4** (MIT) instead.
- ⚠️ **Remotion source-available** — needs paid license above small-team thresholds. **HyperFrames (Apache 2.0)** is the safer commercial pick. Keep Remotion only for already-written compositions.

---

## Per-stage stack (with status)

| Stage | Pick | License | Status | Replaces |
|---|---|---|---|---|
| **Search** | SearXNG (self-hosted meta-search) | AGPL | install pending | Perplexity API |
| **Crawler** | Crawl4AI (Playwright + LLM-native markdown) | Apache 2.0 | install pending | Firecrawl Cloud |
| **LLM (script/judge)** | Ollama (qwen3:8b, qwen2.5-coder:7b, gpt-oss:20b, gemma4, deepseek-r1:8b, danslab-coder:7b) | various OSS | ✅ 13 models on disk | Claude/GPT API |
| **Embeddings** | nomic-embed-text via Ollama | Apache 2.0 | ✅ installed | OpenAI embeddings |
| **Image gen primary** | Flux.1 schnell fp8 via ComfyUI | Apache 2.0 weights | ✅ downloaded (16 GB) | OpenAI gpt-image-1, DALL-E 3, Pollinations |
| **Image gen draft** | SD 1.5 via ComfyUI | community license | ✅ symlinked, verified rendering 31s | — |
| **Image gen GUI** | Draw Things | free Mac app, MLX | ✅ installed | — |
| **Image speed** | SDXL Lightning | Stability community | install on demand | — |
| **Img → Video primary** | Wan 2.2 I2V (cinematic, MoE) | Apache 2.0 | install via ComfyUI Manager | Higgsfield, fal.ai, Seedance |
| **Img → Video heavy lane** | **Comfy Cloud** (paid membership opt-in) | service | active membership | — |
| **Img → Video speed** | LTX-Video | Apache 2.0 | install on demand | — |
| **Frame interpolation** | RIFE | OSS | install in ComfyUI | — |
| **TTS primary (planned upgrade)** | Kokoro 82M | Apache 2.0 | install pending | ElevenLabs Brian |
| **TTS verified default** | Piper en_US-lessac-medium | MIT | ✅ installed (3 copies) | — |
| **TTS voice cloning** | OpenVoice v2 | MIT | install pending | F5-TTS (CC-BY-NC ❌) |
| **TTS fallback** | espeak-ng | GPL-3.0 | ✅ installed | — |
| **STT primary** | WhisperX (alignment + diarization) | Apache 2.0 | install pending | — |
| **STT speed** | faster-whisper (CTranslate2) | MIT | install pending | — |
| **STT current** | OpenAI whisper | MIT | ✅ installed | — |
| **Render primary** | HyperFrames (HTML, agent-first) | Apache 2.0 | ✅ installed + 14 skills active | — |
| **Render secondary** | Remotion (React) | source-available | ✅ installed | keep for legacy scenes only |
| **Math animation** | Manim | MIT | ✅ installed | — |
| **Web motion** | anime.js v4 + motion.dev | MIT | install via npm | GSAP (commercial) |
| **Python assembly** | moviepy + auto-editor | MIT | ✅ installed | — |
| **3D / VFX** | Blender | GPL-3.0 (internal use OK) | ✅ installed | — |
| **NLE (manual polish)** | Shotcut | GPL-3.0 | ✅ installed | — |
| **Video QA technical** | ffprobe + libvmaf (Netflix VMAF) | LGPL | ✅ in ffmpeg 8.1 | — |
| **Video QA frame** | PSNR/SSIM via ffmpeg, LPIPS via Python | various OSS | install on demand | — |
| **Video QA prompt match** | CLIP score via open-clip | MIT | install pending | — |
| **Audio sync** | librosa | ISC | install pending | — |
| **Final mux** | ffmpeg | LGPL | ✅ installed (8.1) | — |
| **Loudness** | ffmpeg loudnorm (EBU R128) | LGPL | ✅ in ffmpeg | — |

---

## Self-improvement layer (already built — see `dashboard/engines/`)

| Module | What it does | Already wired |
|---|---|---|
| `discovery.py` | Daily HuggingFace + GitHub + arXiv scan → 100-tool registry | ✅ |
| `scoring.py` | Cumulative 1-10 score, +1 per locked step, 8+ to advance | ✅ |
| `skill_db.py` | Skill cache: token-set Jaccard match (≥0.55), short-circuits repeat work | ✅ |
| `scheduler.py` | Daemon thread fires discovery hourly when registry stale | ✅ |
| `learnings.py` | Append-only JSONL learning records | ✅ |
| `fleet_dispatch.py` | Routes to Dexter/Memo/Sienna/Nano for review | ✅ |
| `oc_runner.py` + `opencode_client.py` | OpenCode CLI wrapper for local model execution | ✅ |

Planned additions:
- **DSPy** (MIT, Stanford) — programmatic LLM prompt compilation
- **LangGraph** (MIT) — explicit DAG visualization on top of fleet_dispatch
- **Chroma** (Apache 2.0) — vector RAG, already via claude-mem

---

## Paid lanes — explicitly stripped

| File | Action |
|---|---|
| `step_image_gen.py:447` | `DEFAULT_PROVIDER_CHAIN = ['comfyui']` (was 7-provider chain with 4 paid) |
| `step_image_gen.py:554` | `DEFAULT_VIDEO_CHAIN = ['comfyui']` (was 4-provider chain with 3 paid) |
| `step5_audio.py:112` | `_have_elevenlabs_key()` requires `ZMARTY_ALLOW_PAID_TTS=1` opt-in |
| `higgsfield_client.py` | kept as code, never invoked (chain doesn't reference it) |
| `fal_client.py` | kept as code, never invoked |
| `seedance.py` | kept as code, never invoked |

Why "kept as code" instead of deleted: server.py imports them for `/api/providers/status` reporting. Removing requires also editing server.py imports. Safer to gate at the chain level.

---

## Comfy Cloud lane (M11, opt-in)

The user has a paid Comfy Cloud membership for production-grade video renders that exceed local 36 GB Mac RAM. Architecture:

```
Default render → local ComfyUI :8000 (Mac, free, slower for big models)
            └─ if explicit ZMARTY_USE_COMFY_CLOUD=1 → Comfy Cloud API (faster, uses pre-paid credits)
```

- Local stays the default — no silent paid spend.
- Cloud activated per-run via env flag or dashboard toggle.
- Credit balance surfaced in `/api/providers/status` for transparency.
- Use cases: Wan 2.2 video (heavy), Flux dev full res (also heavy), batch renders.

Implementation: TODO. Not yet wired (M11).
