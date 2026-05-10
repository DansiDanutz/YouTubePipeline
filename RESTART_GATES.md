# Zmarty Video Pipeline Restart Gates

Created: 2026-05-04
Owner: Hermes

## Rule
We do not move to the next step unless the current step reaches the required score and the required loop succeeds.

If a score fails, we fix that step only. No skipping forward.

## Step 0 — Recover baseline
Required score: 100/100
Gate:
- Locate working Mac Studio pipeline directory.
- Inventory existing assets, outputs, voice files, scene files, render tools.
- Prove existing render can be inspected with ffprobe.
- Identify missing/blocked dependencies.

Current evidence:
- Pipeline directory: /Users/davidai/Zmarty-Video-Pipeline
- Existing final video: out/zmarty_bitcoin_final.mp4
- Existing AI compute video: out/ai_compute_final.mp4
- Existing scene images: out/preview_scene1-6.jpg and out/ai_preview_scene1-6.jpg, all 1920x1080
- Existing voice/audio: out/yt_narration.mp3, out/ai_narration.wav, narration_piper.wav, narration_fixed.wav
- Render stack: Remotion + FFmpeg + ffprobe available
- ComfyUI CLI/API not currently verified on PATH/API during initial check
- Git is not initialized in this folder

Status: PASS for inventory; dependency gap recorded.

## Step 1 — Lock scoring contract
Required score: 100/100
Gate:
- Define objective scoring categories.
- Define stop/go thresholds.
- Define loop count and retry behavior.
- Store score JSON for every generated asset.

Scoring proposal:
- Script/story relevance: 20
- Prompt-to-image match: 20
- Visual quality: 20
- Scene continuity: 15
- Animation/motion quality: 15
- Technical validity: 10
Pass threshold per scene: 85/100
Hard fail if: missing image, wrong resolution, no animation clip, no requested voice, ffprobe failure, decode failure.

Status: PASS. Implemented in code:
- dashboard/engines/scoring_contract.py
- dashboard/engines/step_image_gen.py now saves prompt sidecars, score JSON sidecars, and stops failed scenes at the image step.
- proof/scoring_contract/STEP1_SCORING_CONTRACT_PROOF.json verifies the gate.
- locked_scene_manifest.json preserves the six user-provided scene prompts before generation.

## Step 2 — Generate images
Required score: every scene >=85/100
Gate:
- One image per scene prompt.
- Save prompt, provider, seed, dimensions, file path.
- Score each image.
- Loop failed images until pass or max retries reached.

## Step 3 — Animate each image
Required score: every clip >=85/100
Gate:
- One video clip per approved image.
- Motion prompt per image.
- Verified duration, fps, resolution, decode.
- Score each clip.

## Step 4 — Voice
Required score: 100/100 technical, >=85 quality
Gate:
- Use the exact requested preconfigured voice, not fallback, unless explicitly approved.
- Save voice provider/name/id proof without exposing secrets.
- Verify audio duration and waveform.

## Step 5 — Edit scenes
Required score: final edit >=90/100
Gate:
- Assemble animated clips, voice, subtitles, music/SFX if requested.
- Scene timing follows narration.
- Render final MP4.
- Full ffprobe + decode QA.

## Step 6 — Open-source pipeline upgrades
Required score: upgrade must improve a measured bottleneck
Gate:
- Research OSS tools only after baseline media pipeline is working.
- Integrate one upgrade at a time.
- Regression-test after each upgrade.
