# ZmartyChat Bitcoin Video — Open Source Stack

## Stack Tehnologic (Toate Open Source)

| Rol | Tool | Licenta |
|---|---|---|
| **Video Editing / Compositing** | Remotion 4.x (React → Video) | MIT |
| **Procedural Animation** | Canvas 2D API + React Hooks | Native |
| **3D Heatmap / Particles** | Canvas 2D (procedural, no Three.js needed) | Native |
| **Base Image Gen** | Flux.1 [dev] (Black Forest Labs) | Open Weights |
| **Video Gen (img2vid)** | Wan 2.1 (Alibaba) | Apache 2.0 |
| **Pipeline Orchestration** | ComfyUI | GPL-3.0 |
| **Encoding / Audio** | FFmpeg 6+ | GPL/LGPL |
| **Type Safety** | TypeScript 5.6+ | Apache 2.0 |

## Scena & Timing (45s total @ 30fps)

| # | Nume | Frame Start | Durata | Tehnologie |
|---|---|---|---|---|
| 1 | Heatmap Hook | 0 | 240f (8s) | Canvas procedural + MeshGradient |
| 2 | Institutional | 240 | 300f (10s) | DataCounter + GlassCard |
| 3 | Futures Decline | 540 | 300f (10s) | Animated bar chart (Canvas) |
| 4 | ETF Inflows | 840 | 210f (7s) | LED ticker CSS + DataCounter |
| 5 | Stability Morph | 1050 | 210f (7s) | Canvas path morph (chaos → stable) |
| 6 | CTA End Card | 1260 | 90f (3s) | Spring animation + brand lockup |

## Quick Start

```bash
# 1. Instalare dependente
npm install

# 2. Preview in studio (live reload)
npm run start

# 3. Render final MP4 (GPU acceleration via FFmpeg)
npm run build

# Output: `out/video.mp4`
```

## Workflow ComfyUI

1. Genereaza base frames cu `flux-base-frames.json` (modifica prompt per scena)
2. Animateaza cu `wan21-img2vid.json` (5 sec motion per clip)
3. Importa clipurile in Remotion ca `<OffthreadVideo>` inlocuind canvas-urile unde vrei realism
4. Render final cu `npm run build`

## De ce acest stack?

- **Remotion** > After Effects pentru date: numerele sunt reale, animatiile precise la frame, timeline programatic.
- **Canvas 2D procedural** > Imagini statice: heatmap-ul si graficele sunt generate matematic, nu mockups.
- **Flux + Wan** > Midjourney/Runway: open weights, self-hostable, cost zero recurent.
- **ComfyUI** > API inchise: noduri vizuale, pipeline reproductibil, nu esti locked-in.
