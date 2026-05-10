# Spec vs. Code — Gap Analysis

Spec source: [WORKFLOW_NOTES.md](WORKFLOW_NOTES.md)
Code surveyed: `dashboard/`, `src/`, `tools/`, `comfyui-workflows/` (438 MB sync, no `node_modules`/`.venv`)

---

## TL;DR

The pipeline is **largely built**. ~11,400 LOC of Python in `dashboard/engines/` implements all 9 step engines (Hermes pre-route → outline → draft → validate → fleet review → convergence loop). Server in `dashboard/server.py` exposes ~40 `/api/stepN/*` routes. Remotion frontend (`src/`) renders the final video.

**The spec calls for 10 steps; code has 9.** The "Add-ons" Step 10 is missing. Several other spec items (scoring loop, Brian/ElevenLabs voice, fal.ai, Seedance, OpenClaude CLI, daily auto-update) are partially built or just placeholders.

---

## Per-step status

| Spec Step | Code Module | LOC | Status | Gaps |
|---|---|---|---|---|
| 1. Prompting | `step1_research.py` | 2218 | ✅ Built — multi-source harvester (GitHub, Reddit, HN, arXiv, HF, Lobsters, etc.), Hermes pre-route, GSD pass, fleet review, learnings store | Spec wants Perplexity MCP — code uses DuckDuckGo/HN/etc. instead. Skill-DB lookup before running ("check pattern → run if exists") is **not** implemented. |
| 2. Narrative & Planning | `step2_script.py` | 1511 | ✅ Built — Hook/Thesis/Evidence×2/Implication/CTA, 88–95 word target, phonetic conversion, fleet review | Spec mentions user-input video length → drives structure; code is hardcoded to ~41s/90 words. |
| 3. Image Generation | `step3_visual.py` + `step_image_gen.py` | 1411 + 480+ | ✅ **Multi-provider chain wired (this session)**: ComfyUI (local Flux) → OpenAI gpt-image-1 ("Image 2") → Siegfried MCP → Pollinations → DALL-E 3. Each provider gracefully reports unavailable when not configured. Chain is overridable via `IMAGE_GEN_PROVIDERS` env var. `image_provider_status()` exposes runtime availability per provider. ✅ **Huashu-design + UI/UX Pro Max skills installed and auto-injected** into every step 3 run via existing `harvest_design_skills()` harvester (line 896 of step3_visual.py) — harvester scans `~/.claude/skills/` and finds both. Verified end-to-end. | Optional: skill-specific prompt templates (Huashu's "5 流派 × 20 设计哲学" recommender) are not directly invoked, but the SKILL.md content is now in the prompt context. |
| 4. Subtitles & Voice | `step5_audio.py` + `step6_subtitles.py` | 1010+ + 970+ | ✅ **ElevenLabs Brian + multi-language subtitles wired (this session)** | Spec puts voice in step 4; code splits voice (5) + subs (6) — keeping that as it's a sensible engineering split. **ElevenLabs**: spec rewrite happens in `_apply_elevenlabs_to_spec()` after the LLM drafts the audio_spec. Voice resolution accepts dashboard labels like `"Brian (deep, Morgan-Freeman style)"`. Live `elevenlabs_synthesize()` call verified — produced 87.8 KB MP3 (128 kbps, 44.1 kHz mono). **Subtitle language**: 17 ISO codes supported (en/es/fr/de/it/pt/ro/pl/nl/tr/ru/uk/zh/ja/ko/ar/hi). `_apply_language_to_spec()` adds `--language <code>` to whisper command, suffixes output filenames for non-English (`subtitles.es.srt`), updates ffmpeg burn-in path, and threads language into the LLM SRT-drafting prompt so subtitle text is generated in the target language. Dashboard exposes a "🌐 Language" selector; `runStep6Pipeline` sends `language` in the fetch body. |
| 5. Scene Planning | `step4_scenes.py` + `seedance.py` | 863 + 140 | ⚠️ **Seedance 2.0 client scaffolded (this session)** — `dashboard/engines/seedance.py` provides `img2vid()` for image→video generation. Reads `SEEDANCE_API_URL` + `SEEDANCE_API_KEY` from env or fleet.env; gracefully skips when unconfigured. Status surfaced via `/api/providers/status`. Wiring into `step4_scenes.py` / `step7_render.py` per-scene loop pending — currently a standalone client. **fal.ai**, **OpenClaude CLI + local model adapters** still not present. |
| 6. Video Generation | (folded into step7) | — | ❌ Missing as separate step | Spec separates "scene video generation" (6) from "editing" (7). Code merges both into `step7_render.py` (Remotion render). No img→video model (Wan 2.1, etc.) is actually wired in — `comfyui-workflows/wan21-img2vid.json` exists but isn't called. **Daily-update mechanism not implemented.** |
| 7. Video Editing | `step7_render.py` | 919 | ✅ Built — Remotion render command, FFmpeg mux, subtitle burn-in | "Once-per-day update if new tools" — `discovery.py` (405 LOC) does daily OSS scan but isn't on a schedule, must be triggered via `/api/discovery/run`. |
| 8. Rendering | (folded into step7) | — | ⚠️ Same as step7 | No separate render-only stage. |
| 9. Final Video | `step8_qa.py` + `step9_final.py` | 839 + 737 | ✅ Built — ffprobe checks, executive review, export manifest, release checklist | Spec wants **GLM/Kimi/GPT** for final verdict — code uses fleet of 4 internal agents (Dexter/Memo/Sienna/Nano). No external LLM verdict. |
| 10. Add-ons | `step10_addons.py` | ~340 | ✅ Built (this session) — Hermes decides if add-ons warranted (intro/outro stings, lower-thirds, brand bumpers, end-card CTAs, social cuts 9×16/1×1, etc.), drafts FFmpeg/Remotion commands, validates, fleet sign-off, increments cumulative_score by 1. Routes: `/api/step10/run`, `/api/step10/advise`, `/api/step10/post_research`. | Commands generated by LLM are not yet auto-executed — produces a spec the user (or a future runner) applies. |

---

## Cross-cutting gaps (spec features not yet in code)

1. **Skill database lookup** — ✅ Built end-to-end (this session). [`dashboard/engines/skill_db.py`](dashboard/engines/skill_db.py) (~210 LOC) + `/api/skills/{find,register,list,delete,summary}` routes. JSONL store at `~/.openclaw/skills/skill_db.jsonl`. Token-set Jaccard similarity match (≥0.55 by default), per-step buckets, auto-deduplication on re-register (updates `use_count` and max-stars). All 10 step engines auto-register a skill on each lock with stars ≥ 4.0. **Frontend integrated for steps 1-9**: every `runStepNPipeline()` in [`dashboard/index.html`](dashboard/index.html) calls `/api/skills/find` before `/api/stepN/run`; on a hit, shows a confirm dialog (score/stars/use_count) and on accept renders the cached result and skips the full pipeline. Step 10 frontend trigger doesn't exist yet (engine + endpoint only).
2. **Predictive scoring system** — ✅ Built end-to-end (this session). `dashboard/engines/scoring.py` (~210 LOC) + `/api/scoring/{get,summary,reset,lock,can_advance}` routes. Per-project JSON store at `~/.openclaw/scores/{project}.json`. Tracks cumulative_score (capped at 10), predicted_score, history per step, advance threshold (8+). **All 10 step engines (1-10) now call `lock_step_from_run(...)` after their convergence loop.** Helper handles both fleet shapes (`fleet.verdicts.{GREEN,YELLOW,RED}` for steps 1-7; `fleet.summary.{greens,yellows,reds}` for steps 8-10). **`project` parameter plumbed through all 10 `run_stepN()` signatures and all 10 `/api/stepN/run` server handlers** — multi-project isolation verified (alpha and beta projects maintain independent score files). Verified end-to-end: clean run → 10/10 cumulative; RED step → `verdict='looped'`, no increment.
3. **Auto skill creation** — ✅ Built (this session). All 10 steps now call `register_skill(...)` immediately after `lock_step_from_run`, with a step-appropriate prompt key (user_input for step 2; script for steps 4-6; subject/manifest for step 7; output_file+scene count for step 8; output_file+verdict for step 9; output_file+verdict for step 10). Each registration includes a small `result_excerpt` so cache hits can short-circuit downstream work. Verified: 10/10 steps register, all imports present, single call per step.
4. **Learning-agent + Auto-research-agent updates** — `learnings.py` writes JSONL records but doesn't generate skill markdown, doesn't update general workflow docs.
   - ✅ **Bug fix this session**: `record_learning()` previously only accepted a single `dict` argument, but steps 8, 9, and 10 called it with kwargs (`kind=..., summary=..., what_worked=...`). Those calls silently TypeError'd inside `try/except Exception: pass` — meaning step 8/9/10 learnings were never being persisted. Signature now accepts both dict and kwargs. Steps 1–7 (dict form) still work; steps 8–10 (kwargs form) now record correctly.
5. **Hermes Agent + OpenClaw Agent as named entities** — ✅ Resolved on inspection. `dashboard/engines/fleet_dispatch.py` opens with "bridge between the Step 1 research brief and David's OpenClaw fleet (Dexter / Memo / Sienna / Nano) via Hermes-style routing" and references both `Hermes HCI (http://localhost:10272)` and `OpenClaw gateway (http://localhost:18789)`. The spec's "Hermes Agent" maps to the pre-route stage in every step engine; "OpenClaw Agent" is the umbrella term for the 4-specialist fleet. No misalignment — just terminology overlap. Confirmed by reading the existing module header.
6. **OpenClaude CLI / local-model-only constraint** — Spec: "Never use API per usage or Claude subscriptions to generate. We need to keep it low cost BUT highest quality." Code calls Pollinations.ai + DALL-E 3 (paid). No local SDXL/Flux invocation wired in. Note: Step 5 now adds ElevenLabs (paid API) as primary TTS — this contradicts the strict local-only reading of the spec, but the spec also explicitly names "Brian from ElevenLabs" as the required voice, so this is treated as a deliberate per-step exception.
7. **fal.ai integration** — Spec mentions as fallback. No fal.ai client code.
8. **Daily auto-discovery loop** — ✅ Built (this session). [`dashboard/engines/scheduler.py`](dashboard/engines/scheduler.py) (~150 LOC) starts a daemon thread inside the dashboard process. On boot (after 30s delay), polls every hour; fires `run_discovery()` when the on-disk registry is missing or older than 24h. Persists timestamps, tracks fire/check counts, idempotent (second start is a no-op). `/api/scheduler/status` returns `{running, registry_age_hrs, stale, fires, checks, last_fire_at, last_fire_result, ...}`. `/api/scheduler/force_run` for manual override. No external cron required, Windows-friendly. Verified live in running server.
9. **Output naming convention** — ✅ Built (this session). [`step7_render.py`](dashboard/engines/step7_render.py) now exposes `subject: str = ''` parameter on `run_step7()`. When provided, `_apply_spec_filename()` rewrites `output_file`, `ffmpeg_burn_subtitles`, `ffmpeg_no_subtitles`, and `ffprobe_validate` to use `out/<slug>_<YYYYMMDD>_<HHMMSS>.mp4`. Slug derived from subject (alphanumeric only, hyphenated, lowercase, max 60 chars). Empty subject → legacy `out/final.mp4` (zero-disruption fallback). Server route `/api/step7/run` accepts `subject` or falls back to `prompt` from request body. Example output: `out/bitcoin-institutional_20260504_124228.mp4`.

---

## What IS built that goes beyond the spec

- **Project switcher** (`projects.py`) — multiple concurrent video projects, switch context.
- **Fleet dispatch** (`fleet_dispatch.py`) — 4 named specialist agents reviewing each step.
- **GDS validators** — hard validators per step (word count, hex codes, CPS limits, frame math, ffprobe checks). These are the "gates" the spec talks about.
- **Convergence loops** — each step re-drafts when fleet flags RED. This IS the spec's "loop until 8+" mechanism, just expressed differently.
- **Discovery registry** — `discovery.py` finds new HF models / GitHub repos / arXiv papers and injects into step prompts (the "auto-research" function from spec).
- **Dashboard UI** — single 7,582-line `index.html` with all step controls.

---

## Recommended next moves (in order)

1. **Get it running locally** — `npm install` + create `.venv` and check `dashboard/server.py` imports to find Python deps (no `requirements.txt` in repo).
2. **Decide on naming**: rename internal "Dexter/Memo/Sienna/Nano" fleet to match spec's "Hermes + OpenClaw" — OR keep the existing names and document in spec.
3. **Add Step 10 module + skill-DB lookup** — these are the two clearest spec items missing entirely.
4. **Wire ElevenLabs Brian voice** as primary in `step5_audio.py` (Piper as fallback).
5. **Add explicit 1–10 scoring + +1-per-step accumulator** to `fleet_dispatch.py` so the spec's threshold gating works.
6. **Wire `tools/` for local image/video gen** — folder is 313 MB but only contains `piper/` and `whisper/` currently. To honor "no paid APIs" you'd need local Flux/SDXL + Wan/Seedance.
