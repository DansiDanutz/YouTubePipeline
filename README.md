# YouTubePipeline

**OSS-only, locally-runnable, gated YouTube video production pipeline.** Built for Dan's Lab; designed to scale to a paid-membership product.

> The whole pipeline runs without sending a single byte to a paid API. ComfyUI (Flux/SDXL/Wan) for image+video, Piper for voice, Whisper for subtitles, Remotion+ffmpeg for compositing, Ollama for LLM. Optional **Comfy Cloud** lane for production-grade Wan 2.2 video renders that exceed local Mac RAM.

---

## Status

**Working today:**
- ✅ 10-step pipeline (research → script → visual → scenes → audio → subtitles → render → QA → final → addons)
- ✅ ~13,000 LOC across 31 engine modules in `dashboard/engines/`
- ✅ 67 API routes, single dashboard UI at port 8766
- ✅ Scoring contract gates every step (per-scene ≥85, hard-fail conditions, no skip-forward)
- ✅ Daily OSS discovery loop (HuggingFace + GitHub + arXiv → `~/.openclaw/oss_registry.json`)
- ✅ Skill cache: "have we solved this before?" check against past runs
- ✅ Multi-project workspaces, fleet review (Dexter/Memo/Sienna/Nano specialists)
- ✅ Local model: SD 1.5 verified rendering at 768×432 in 31s
- ✅ Local model: Flux schnell fp8 (Apache 2.0) downloaded — production-quality, opt-in

**OSS-only conversion (May 2026):**
- Image chain → `['comfyui']` only (paid lanes ripped: gpt-image-1, dalle-3, fal, higgsfield, pollinations)
- Video chain → `['comfyui', 'local-ffmpeg']` only (paid lanes ripped: seedance, fal, higgsfield)
- Voice → Piper en_US-lessac-medium primary; ElevenLabs gated behind explicit `ZMARTY_ALLOW_PAID_TTS=1` opt-in

See [OSS_STACK.md](OSS_STACK.md) for the full per-stage technology choices.

---

## Quick start

```bash
# 1. Set up Python env
python3.13 -m venv .venv && source .venv/bin/activate
# Engine deps (will be formalized in requirements.txt soon):
pip install moviepy manim crawl4ai whisperx faster-whisper kokoro-onnx open-clip-torch lpips librosa dspy-ai

# 2. Start ComfyUI (separate process)
#    Install Mac app from comfy.org or use Python source at ~/ComfyUI
open -a ComfyUI   # listens on port 8000 by default

# 3. Start the dashboard
COMFYUI_HOST=http://127.0.0.1:8000 python3.13 dashboard/server.py 8766

# 4. Open the dashboard
open http://127.0.0.1:8766
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  DASHBOARD UI (dashboard/index.html, 9551 lines)            │
│  Step 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 panels         │
└──────────────────┬──────────────────────────────────────────┘
                   │ /api/stepN/{run, advise, post_research}
┌──────────────────▼──────────────────────────────────────────┐
│  DASHBOARD SERVER (dashboard/server.py)                      │
│  • 67 routes  • secret hydration (Keychain + fleet.env)      │
└──────────────────┬──────────────────────────────────────────┘
                   │
       ┌───────────┴────────────┬──────────────────┬──────────────┐
       ▼                        ▼                  ▼              ▼
┌─────────────┐         ┌──────────────┐    ┌────────────┐  ┌──────────┐
│ Step engines│         │ Cross-cutting│    │ Providers  │  │ Storage  │
│ (10 modules)│         │ subsystems   │    │            │  │          │
├─────────────┤         ├──────────────┤    ├────────────┤  ├──────────┤
│ research    │         │ scoring      │    │ ComfyUI    │  │ projects │
│ script      │         │ skill_db     │    │ (Flux/SDXL │  │ scenes   │
│ visual      │  ←───→  │ discovery    │    │  /Wan local│  │ scoring  │
│ scenes      │         │ scheduler    │    │  +Cloud)   │  │ skills   │
│ audio       │         │ learnings    │    │ Piper TTS  │  │ learnings│
│ subtitles   │         │ fleet_disp.  │    │ Whisper STT│  │ registry │
│ render      │         │ projects     │    │ ffmpeg     │  │          │
│ qa          │         │ scenes       │    │ Remotion   │  │          │
│ final       │         │ secrets      │    │            │  │          │
│ addons      │         │ oc_runner    │    │            │  │          │
└─────────────┘         └──────────────┘    └────────────┘  └──────────┘
```

---

## Gating contract

Per `RESTART_GATES.md`: **we do not move to the next step unless the current step reaches the required score.**

| Step | Required score | Hard fail if |
|---|---|---|
| 0 (baseline) | 100/100 | inventory + ffprobe inspectable |
| 1 (scoring contract) | 100/100 | contract loaded, gates active |
| 2 (image gen) | every scene ≥ 85/100 | missing image, wrong res, low CLIP score |
| 3 (animation) | every clip ≥ 85/100 | wrong fps, wrong duration, decode failure |
| 4 (voice) | 100 tech, ≥85 quality | wrong voice id, missing audio |
| 5 (edit) | final ≥ 90/100 | ffprobe failure, audio desync |
| 6 (OSS upgrades) | upgrade improves bottleneck | no regression vs baseline |

---

## Repository layout

```
YouTubePipeline/
├── dashboard/             # The 10-step engine
│   ├── server.py          # 972 LOC, custom http.server, 67 routes
│   ├── index.html         # 9551 LOC single-page UI
│   └── engines/           # 31 modules (step1-10 + cross-cutting)
├── src/                   # Remotion compositions (zmarty/ai/yt scenes)
├── comfyui-workflows/     # Flux + Wan2.1 ComfyUI JSON workflows
├── tools/                 # Piper voices + Whisper binaries
├── runs/                  # Per-project run artifacts (gitignored)
├── out/                   # Rendered MP4s (gitignored)
├── docs/                  # Strategic + technical docs
├── .agents/skills/        # 14 HyperFrames skills (animejs, gsap, lottie, three, waapi, etc.)
├── README.md              # This file
├── OSS_STACK.md           # Full OSS technology stack
├── RESTART_GATES.md       # Gating contract (non-negotiable)
└── GAP_ANALYSIS.md        # Spec vs. code coverage
```

---

## Built by

Dan's Lab (DansiDanutz). David orchestrates, Hermes routes, Dexter / Memo / Sienna / Nano specialists review.
