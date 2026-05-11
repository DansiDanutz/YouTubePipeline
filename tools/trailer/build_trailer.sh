#!/bin/bash
# 30-second trailer builder — Cloud Flux DEV images + ffmpeg Ken Burns + macOS narration
#
# Verified live 2026-05-11: produces 1920x1080 H.264+AAC MP4 in ~3 minutes.
# Cost: ~6 Cloud Flux DEV credits per trailer. Output: 30.000s exact.
#
# Usage:
#   tools/trailer/build_trailer.sh [scenes.json] [output_dir]
#
# Defaults: tools/trailer/scenes.json + ~/Documents/ComfyUI/output/trailer
set -e

SCENES="${1:-$(dirname "$0")/scenes.json}"
OUT="${2:-$HOME/Documents/ComfyUI/output/trailer}"
KEY="$(security find-generic-password -s 'COMFY_CLOUD_API_KEY' -w 2>/dev/null)"
[ -z "$KEY" ] && { echo "ERROR: COMFY_CLOUD_API_KEY not in Keychain"; exit 1; }
[ ! -f "$SCENES" ] && { echo "ERROR: scenes.json not found at $SCENES"; exit 1; }
mkdir -p "$OUT"

NARRATION="${TRAILER_NARRATION:-What if your company never slept? At Dan's Lab, we don't just build A I agents. We build a fleet of them. Dexter writes the code. Memo runs operations. Sienna trades crypto in real time. Nano spawns new agents on demand. Every day, they ship. Every night, they learn. This isn't automation. This is autonomy. Dan's Lab. The future of work, already working today.}"
SAY_VOICE="${SAY_VOICE:-Alex}"
SAY_RATE="${SAY_RATE:-175}"
COMFY_CLOUD_BASE="${COMFY_CLOUD_BASE:-https://cloud.comfy.org}"
CHECKPOINT="${COMFY_CLOUD_DEFAULT_CHECKPOINT:-flux1-dev-fp8.safetensors}"
SCENE_COUNT=$(python3 -c "import json; print(len(json.load(open('$SCENES'))))")

# ─── Step 1: submit all renders to Cloud (parallel queue) ─────────────────
echo "===Submit $SCENE_COUNT Flux DEV renders to Cloud==="
for i in $(seq 1 $SCENE_COUNT); do
  PROMPT=$(python3 -c "import json; d=json.load(open('$SCENES')); print([x for x in d if x['id']==$i][0]['prompt'])")
  WORKFLOW=$(python3 -c "
import json
prompt = '''$PROMPT'''
wf = {
  '1': {'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'$CHECKPOINT'}},
  '2': {'class_type':'EmptySD3LatentImage','inputs':{'width':1920,'height':1080,'batch_size':1}},
  '3': {'class_type':'CLIPTextEncode','inputs':{'clip':['1',1],'text': prompt}},
  '4': {'class_type':'CLIPTextEncode','inputs':{'clip':['1',1],'text':''}},
  '5': {'class_type':'KSampler','inputs':{'model':['1',0],'positive':['3',0],'negative':['4',0],'latent_image':['2',0],'seed':$((i*100+42)),'steps':20,'cfg':1.0,'sampler_name':'euler','scheduler':'simple','denoise':1.0}},
  '6': {'class_type':'VAEDecode','inputs':{'samples':['5',0],'vae':['1',2]}},
  '7': {'class_type':'SaveImage','inputs':{'images':['6',0],'filename_prefix':'trailer_scene_$i'}},
}
print(json.dumps({'prompt': wf}))
")
  PID=$(curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
    --data "$WORKFLOW" "$COMFY_CLOUD_BASE/api/prompt" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt_id',''))")
  echo "  scene $i -> $PID"
  echo "$PID" > "$OUT/scene_${i}.pid"
done

# ─── Step 2: poll each + download ──────────────────────────────────────────
echo
echo "===Poll + download==="
for i in $(seq 1 $SCENE_COUNT); do
  PID=$(cat "$OUT/scene_${i}.pid")
  for tries in $(seq 1 90); do
    STATUS=$(curl -s -H "X-API-Key: $KEY" "$COMFY_CLOUD_BASE/api/jobs/$PID" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
    if [ "$STATUS" = "completed" ]; then
      FNAME=$(curl -s -H "X-API-Key: $KEY" "$COMFY_CLOUD_BASE/api/jobs/$PID" \
        | python3 -c "
import sys,json
d=json.load(sys.stdin)
for nid, no in d.get('outputs',{}).items():
  for img in no.get('images',[]):
    print(img.get('filename')); sys.exit()
")
      curl -sL -H "X-API-Key: $KEY" "$COMFY_CLOUD_BASE/api/view?filename=$FNAME" -o "$OUT/scene_${i}.png"
      echo "  scene $i ✅ $(du -h "$OUT/scene_${i}.png" | awk '{print $1}')"
      break
    fi
    [ "$STATUS" = "failed" ] && { echo "  scene $i ❌ FAILED"; break; }
    sleep 2
  done
done

# ─── Step 3: narration ─────────────────────────────────────────────────────
# Priority order:
#   1. ElevenLabs Brian (Flash v2.5) — when ELEVENLABS_API_KEY in Keychain
#   2. macOS 'say' Alex — fallback (always available, basic quality)
# Override Brian voice id with $ELEVENLABS_VOICE_BRIAN; override model with
# $ELEVENLABS_MODEL_ID (default: eleven_flash_v2_5).
echo
ELEVEN_KEY="$(security find-generic-password -s 'ELEVENLABS_API_KEY' -w 2>/dev/null)"
BRIAN_ID="${ELEVENLABS_VOICE_BRIAN:-nPczCjzI2devNBz1zQrb}"
ELEVEN_MODEL="${ELEVENLABS_MODEL_ID:-eleven_flash_v2_5}"
if [ -n "$ELEVEN_KEY" ]; then
  echo "===Narration via ElevenLabs Brian ($ELEVEN_MODEL)==="
  ELEVEN_JSON=$(python3 -c "
import json, sys
print(json.dumps({
  'text': sys.argv[1],
  'model_id': sys.argv[2],
  'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75, 'style': 0.4, 'use_speaker_boost': True},
}))" "$NARRATION" "$ELEVEN_MODEL")
  CODE=$(curl -s -X POST "https://api.elevenlabs.io/v1/text-to-speech/$BRIAN_ID" \
    -H "xi-api-key: $ELEVEN_KEY" -H "Content-Type: application/json" -d "$ELEVEN_JSON" \
    -o "$OUT/narration.mp3" -w "%{http_code}")
  if [ "$CODE" = "200" ]; then
    ffmpeg -y -i "$OUT/narration.mp3" -c:a aac -b:a 192k "$OUT/narration.aac" 2>/dev/null
    NARR_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT/narration.aac")
    echo "  narration: ${NARR_DUR}s (Brian, premium)"
  else
    echo "  ⚠️ ElevenLabs returned HTTP $CODE — falling back to macOS say"
    ELEVEN_KEY=""
  fi
fi
if [ -z "$ELEVEN_KEY" ]; then
  echo "===Narration via macOS 'say' ($SAY_VOICE @ $SAY_RATE wpm)==="
  say -v "$SAY_VOICE" -r "$SAY_RATE" -o "$OUT/narration.aiff" "$NARRATION"
  ffmpeg -y -i "$OUT/narration.aiff" -c:a aac -b:a 192k "$OUT/narration.aac" 2>/dev/null
  NARR_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT/narration.aac")
  echo "  narration: ${NARR_DUR}s (macOS Alex, basic)"
fi

# ─── Step 4: HD Ken Burns motion clips (1920x1080) ─────────────────────────
echo
echo "===Build HD Ken Burns motion clips (1920x1080)==="
ken_burns_hd() {
  local src="$1" dst="$2" sec="$3" motion="$4"
  local frames=$((sec * 25))
  local VF
  case "$motion" in
    zoom_in)      VF="zoompan=z='min(zoom+0.0009,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=$frames:fps=25,scale=1920:1080:flags=lanczos" ;;
    zoom_out)     VF="zoompan=z='if(eq(in,1),1.2,zoom-0.0008)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=$frames:fps=25,scale=1920:1080:flags=lanczos" ;;
    pan_right)    VF="zoompan=z=1.1:x='iw*0.05+iw*0.05*on/$frames':y='ih/2-(ih/zoom/2)':d=$frames:fps=25,scale=1920:1080:flags=lanczos" ;;
    pan_left)     VF="zoompan=z=1.1:x='iw*0.1-iw*0.05*on/$frames':y='ih/2-(ih/zoom/2)':d=$frames:fps=25,scale=1920:1080:flags=lanczos" ;;
    zoom_in_slow) VF="zoompan=z='min(zoom+0.0004,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=$frames:fps=25,scale=1920:1080:flags=lanczos" ;;
  esac
  ffmpeg -y -loop 1 -i "$src" -vf "$VF" -t "$sec" -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -r 25 "$dst" 2>/dev/null
}
for i in $(seq 1 $SCENE_COUNT); do
  SEC=$(python3 -c "import json; d=json.load(open('$SCENES')); print([x for x in d if x['id']==$i][0]['duration'])")
  MOTION=$(python3 -c "import json; d=json.load(open('$SCENES')); print([x for x in d if x['id']==$i][0]['motion'])")
  ken_burns_hd "$OUT/scene_${i}.png" "$OUT/clip_${i}.mp4" "$SEC" "$MOTION"
  echo "  clip $i: ${SEC}s ($MOTION)"
done

# ─── Step 5: concat + mux narration ────────────────────────────────────────
echo
echo "===Concat + mux==="
> "$OUT/concat.txt"
for i in $(seq 1 $SCENE_COUNT); do echo "file 'clip_${i}.mp4'" >> "$OUT/concat.txt"; done
ffmpeg -y -f concat -safe 0 -i "$OUT/concat.txt" -c copy "$OUT/video_no_audio.mp4" 2>/dev/null
ffmpeg -y -i "$OUT/video_no_audio.mp4" -i "$OUT/narration.aac" \
  -c:v copy -c:a aac -b:a 192k -movflags +faststart "$OUT/trailer.mp4" 2>/dev/null

echo
echo "===FINAL TRAILER: $OUT/trailer.mp4 ==="
ls -lh "$OUT/trailer.mp4"
ffprobe -v error -select_streams v -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of default=nk=1:nw=0 "$OUT/trailer.mp4"
