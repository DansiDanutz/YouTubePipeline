import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
  Img,
  OffthreadVideo,
  staticFile,
} from "remotion";
import { TOKENS, FilmGrade } from "./StoryScenes";

// ────────────────────────────────────────────────────────────────────────────
// AI-image-driven scenes — each scene displays a generated hero image
// with editorial text overlay (eyebrow + headline + stat).
//
// Image URL is passed in as `imageUrl` prop — relative path to the project's
// public/ directory or absolute http URL.
//
// Animation pattern:
//   - Image: Ken Burns (slow pan + scale up 1.0 → 1.06 over the scene duration)
//   - Text: enter with spring, sit with vignette, exit with fade
//   - Slight blur during transitions (handled by TransitionSeries)
// ────────────────────────────────────────────────────────────────────────────

type ImageBeatBase = {
  imageUrl?: string;     // path to generated image relative to public/
  motionClip?: string;   // path to generated motion clip relative to public/
  motion_clip?: string;  // backend alias before JSON normalization
  duration_s?: number;   // total scene duration (for Ken Burns timing)
};

const mediaSrc = (src?: string) => {
  if (!src) return "";
  if (/^(https?:|data:|blob:|file:)/.test(src)) return src;
  return staticFile(src.replace(/\\/g, "/").replace(/^public\//, "").replace(/^\/+/, ""));
};

// Kind-specific overlay shapes
export type ImageBeat =
  | (ImageBeatBase & { kind: "hook"; eyebrow: string; statValue: number; statSuffix?: string; statPrefix?: string; label: string; subLabel?: string })
  | (ImageBeatBase & { kind: "setup"; eyebrow: string; mark: string; name: string; subtitle: string; tags: string[] })
  | (ImageBeatBase & { kind: "conflict"; eyebrow: string; headline: string; bars: { label: string; value: number; unit: string; color?: string }[]; total: { label: string; value: string } })
  | (ImageBeatBase & { kind: "breakthrough"; eyebrow: string; headline: string; rows: { rank: number; name: string; value: string; lang?: string; hero?: boolean }[] })
  | (ImageBeatBase & { kind: "resolution"; eyebrow: string; headline: string; bigStat: string; bigStatLabel: string; stats: { v: string; l: string }[] })
  | (ImageBeatBase & { kind: "cta"; eyebrow: string; headline: string; url: string });

// ────────────────────────────────────────────────────────────────────────────
// HeroImage — Ken Burns slow zoom on the AI image with vignette overlay
// ────────────────────────────────────────────────────────────────────────────

const HeroImage: React.FC<{ imageUrl?: string; motionClip?: string; durationFrames: number; tone?: "warm" | "cool" }> = ({ imageUrl, motionClip, durationFrames, tone = "warm" }) => {
  const frame = useCurrentFrame();
  // Ken Burns: scale 1.0 → 1.08 across full scene, slow pan -2% → +2% on x
  const scale = interpolate(frame, [0, durationFrames], [1.0, 1.08], { extrapolateRight: "clamp" });
  const x = interpolate(frame, [0, durationFrames], [-2, 2], { extrapolateRight: "clamp" });
  const enter = interpolate(frame, [0, 14], [0, 1], { extrapolateRight: "clamp" });

  const resolvedVideo = mediaSrc(motionClip);
  const resolvedImage = mediaSrc(imageUrl);
  const resolvedMedia = resolvedVideo || resolvedImage;

  if (!resolvedMedia) {
    return <AbsoluteFill style={{ background: TOKENS.bg }} />;
  }

  return (
    <AbsoluteFill style={{ overflow: "hidden", background: TOKENS.bg }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          transform: `translateX(${x}%) scale(${scale})`,
          opacity: enter,
        }}
      >
        {resolvedVideo ? (
          <OffthreadVideo src={resolvedVideo} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        ) : (
          <Img src={resolvedImage} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        )}
      </div>
      {/* Bottom-up gradient for legibility of text overlay */}
      <AbsoluteFill style={{
        background: tone === "warm"
          ? "linear-gradient(180deg, rgba(10,10,12,0.45) 0%, rgba(10,10,12,0.20) 35%, rgba(10,10,12,0.55) 70%, rgba(10,10,12,0.92) 100%)"
          : "linear-gradient(180deg, rgba(10,12,18,0.55) 0%, rgba(10,12,18,0.25) 40%, rgba(10,12,18,0.65) 75%, rgba(10,12,18,0.95) 100%)",
        pointerEvents: "none",
      }}/>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 1 · IMAGE-HOOK — big counter overlaid on hero image
// ────────────────────────────────────────────────────────────────────────────
export const ImageHook: React.FC<{ data: Extract<ImageBeat, { kind: "hook" }>; durationFrames: number }> = ({ data, durationFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - 18, fps, config: { damping: 18, stiffness: 90 } });
  const value = Math.floor(interpolate(frame, [25, 130], [0, data.statValue], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: (t) => 1 - Math.pow(1 - t, 4),
  }));
  const labelOp = interpolate(frame, [110, 140], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill>
      <HeroImage imageUrl={data.imageUrl} motionClip={data.motionClip || data.motion_clip} durationFrames={durationFrames} tone="warm" />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "flex-end", paddingBottom: 120 }}>
        <div style={{
          textAlign: "center", color: TOKENS.gold, fontFamily: TOKENS.mono, fontSize: 18,
          letterSpacing: "0.4em", opacity: interpolate(frame, [10, 35], [0, 1], { extrapolateRight: "clamp" }),
          textShadow: "0 1px 8px rgba(0,0,0,0.8)", marginBottom: 24,
        }}>
          {data.eyebrow}
        </div>
        <div style={{
          textAlign: "center", color: TOKENS.gold, fontSize: 200, fontWeight: 800,
          fontFamily: TOKENS.display, fontFeatureSettings: '"tnum"', fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.04em", textShadow: "0 6px 30px rgba(0,0,0,0.85), 0 0 60px rgba(250,204,21,0.35)",
          opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [40, 0])}px)`,
        }}>
          {data.statPrefix || ""}{value.toLocaleString()}{data.statSuffix || ""}
        </div>
        <div style={{
          textAlign: "center", color: TOKENS.ink, fontSize: 32, fontWeight: 600, marginTop: 16,
          opacity: labelOp, fontFamily: TOKENS.display, textShadow: "0 1px 12px rgba(0,0,0,0.85)",
        }}>
          {data.label}
        </div>
        {data.subLabel && (
          <div style={{
            textAlign: "center", color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 16,
            letterSpacing: "0.15em", marginTop: 12, opacity: labelOp,
          }}>
            {data.subLabel}
          </div>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 2 · IMAGE-SETUP — protagonist card overlaid on environment image
// ────────────────────────────────────────────────────────────────────────────
export const ImageSetup: React.FC<{ data: Extract<ImageBeat, { kind: "setup" }>; durationFrames: number }> = ({ data, durationFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - 8, fps, config: { damping: 20 } });
  const nameEnter = spring({ frame: frame - 28, fps, config: { damping: 18 } });
  const tagsEnter = interpolate(frame, [55, 95], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill>
      <HeroImage imageUrl={data.imageUrl} motionClip={data.motionClip || data.motion_clip} durationFrames={durationFrames} tone="warm" />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center" }}>
        <div style={{
          color: TOKENS.gold, fontFamily: TOKENS.mono, fontSize: 16,
          letterSpacing: "0.4em", opacity: enter, marginBottom: 28,
          textShadow: "0 1px 8px rgba(0,0,0,0.85)",
        }}>
          {data.eyebrow}
        </div>
        <div style={{
          width: 130, height: 130, border: `2.5px solid ${TOKENS.ink}`, borderRadius: "50%",
          display: "flex", alignItems: "center", justifyContent: "center", color: TOKENS.ink,
          fontSize: 56, fontWeight: 700, fontFamily: TOKENS.mono, opacity: enter,
          background: "rgba(10,10,12,0.55)", backdropFilter: "blur(12px)",
          boxShadow: "0 0 40px rgba(0,0,0,0.6)",
        }}>
          {data.mark}
        </div>
        <div style={{
          color: TOKENS.ink, fontSize: 70, fontWeight: 700, marginTop: 32,
          letterSpacing: "-0.025em", fontFamily: TOKENS.display,
          opacity: nameEnter, transform: `translateY(${interpolate(nameEnter, [0, 1], [20, 0])}px)`,
          textShadow: "0 2px 16px rgba(0,0,0,0.85)",
        }}>
          {data.name}
        </div>
        <div style={{
          color: TOKENS.muted, fontSize: 24, marginTop: 8, fontFamily: TOKENS.display,
          opacity: nameEnter, textShadow: "0 1px 8px rgba(0,0,0,0.8)",
        }}>
          {data.subtitle}
        </div>
        <div style={{ display: "flex", gap: 24, marginTop: 36, opacity: tagsEnter }}>
          {data.tags.map((t) => (
            <div key={t} style={{
              padding: "10px 22px", border: `1px solid ${TOKENS.rule}`, borderRadius: 4,
              color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 15, letterSpacing: "0.15em",
              background: "rgba(10,10,12,0.55)", backdropFilter: "blur(8px)",
            }}>
              {t}
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 3 · IMAGE-CONFLICT — bars overlay on tension image
// ────────────────────────────────────────────────────────────────────────────
export const ImageConflict: React.FC<{ data: Extract<ImageBeat, { kind: "conflict" }>; durationFrames: number }> = ({ data, durationFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame: frame - 8, fps, config: { damping: 20 } });
  const totalEnter = interpolate(frame, [Math.floor(durationFrames * 0.7), Math.floor(durationFrames * 0.85)], [0, 1], { extrapolateRight: "clamp" });
  const maxBar = Math.max(...data.bars.map(b => b.value), 1);

  return (
    <AbsoluteFill>
      <HeroImage imageUrl={data.imageUrl} motionClip={data.motionClip || data.motion_clip} durationFrames={durationFrames} tone="warm" />
      <AbsoluteFill style={{ paddingTop: 80, paddingLeft: 80, paddingRight: 80 }}>
        <div style={{
          color: TOKENS.danger, fontFamily: TOKENS.mono, fontSize: 16,
          letterSpacing: "0.4em", opacity: titleEnter,
          textShadow: "0 1px 6px rgba(0,0,0,0.9)",
        }}>
          {data.eyebrow}
        </div>
        <div style={{
          color: TOKENS.ink, fontSize: 56, fontWeight: 700, marginTop: 12,
          letterSpacing: "-0.025em", fontFamily: TOKENS.display,
          opacity: titleEnter, textShadow: "0 2px 14px rgba(0,0,0,0.9)",
        }}>
          {data.headline}
        </div>
        <div style={{ marginTop: 80 }}>
          {data.bars.map((b, i) => {
            const barEnter = spring({ frame: frame - (30 + i * 16), fps, config: { damping: 14, stiffness: 70 } });
            const w = (b.value / maxBar) * 80;
            const c = b.color || (i % 2 === 0 ? TOKENS.gold : TOKENS.warm);
            return (
              <div key={b.label} style={{ marginBottom: 22 }}>
                <div style={{ display: "flex", alignItems: "baseline", marginBottom: 6 }}>
                  <span style={{ color: TOKENS.ink, fontFamily: TOKENS.mono, fontSize: 15, width: 240, letterSpacing: "0.15em", textShadow: "0 1px 4px rgba(0,0,0,0.9)" }}>{b.label}</span>
                  <span style={{ color: c, fontSize: 26, fontWeight: 700, fontVariantNumeric: "tabular-nums", textShadow: "0 1px 6px rgba(0,0,0,0.9)" }}>{b.value}{b.unit}</span>
                </div>
                <div style={{
                  height: 36, width: `${barEnter * w}%`,
                  background: `linear-gradient(90deg, ${c} 0%, ${c}aa 100%)`,
                  borderRadius: 2, boxShadow: `0 0 24px ${c}66`,
                }} />
              </div>
            );
          })}
        </div>
        <div style={{
          marginTop: 32, color: TOKENS.danger, fontFamily: TOKENS.mono,
          fontSize: 18, letterSpacing: "0.1em", opacity: totalEnter,
          textShadow: "0 1px 6px rgba(0,0,0,0.9)",
        }}>
          {data.total.label} · {data.total.value}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 4 · IMAGE-BREAKTHROUGH — leaderboard overlay on triumphant image
// ────────────────────────────────────────────────────────────────────────────
export const ImageBreakthrough: React.FC<{ data: Extract<ImageBeat, { kind: "breakthrough" }>; durationFrames: number }> = ({ data, durationFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame: frame - 8, fps, config: { damping: 20 } });

  return (
    <AbsoluteFill>
      <HeroImage imageUrl={data.imageUrl} motionClip={data.motionClip || data.motion_clip} durationFrames={durationFrames} tone="warm" />
      <AbsoluteFill style={{ paddingTop: 70, paddingLeft: 80, paddingRight: 80 }}>
        <div style={{
          color: TOKENS.gold, fontFamily: TOKENS.mono, fontSize: 16,
          letterSpacing: "0.4em", opacity: titleEnter,
          textShadow: "0 1px 6px rgba(0,0,0,0.9)",
        }}>
          {data.eyebrow}
        </div>
        <div style={{
          color: TOKENS.ink, fontSize: 52, fontWeight: 700, marginTop: 10,
          letterSpacing: "-0.025em", fontFamily: TOKENS.display,
          opacity: titleEnter, textShadow: "0 2px 14px rgba(0,0,0,0.9)",
        }}>
          {data.headline}
        </div>
        <div style={{ marginTop: 60, background: "rgba(10,10,12,0.5)", backdropFilter: "blur(10px)", padding: 24, borderRadius: 4 }}>
          {data.rows.map((r, i) => {
            const rowEnter = spring({ frame: frame - (35 + i * 20), fps, config: { damping: 16, stiffness: 90 } });
            const isHero = r.hero;
            return (
              <div key={r.rank} style={{
                display: "flex", alignItems: "center",
                padding: isHero ? "16px 24px" : "12px 24px",
                borderBottom: `1px solid ${TOKENS.rule}`,
                background: isHero ? "rgba(250, 204, 21, 0.08)" : "transparent",
                opacity: rowEnter, transform: `translateX(${interpolate(rowEnter, [0, 1], [-30, 0])}px)`,
              }}>
                <div style={{ width: 50, color: isHero ? TOKENS.gold : TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 18, fontWeight: 600 }}>#{r.rank}</div>
                <div style={{ flex: 1, color: isHero ? TOKENS.ink : TOKENS.muted, fontFamily: TOKENS.mono, fontSize: isHero ? 22 : 18, fontWeight: isHero ? 600 : 400 }}>{r.name}</div>
                {r.lang && <div style={{ width: 100, color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 14, textAlign: "right" }}>{r.lang}</div>}
                <div style={{
                  width: 200, textAlign: "right", color: isHero ? TOKENS.gold : TOKENS.ink,
                  fontSize: isHero ? 38 : 28, fontWeight: 700, fontVariantNumeric: "tabular-nums",
                }}>
                  {r.value}
                </div>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 5 · IMAGE-RESOLUTION — big stat overlay on epic resolution image
// ────────────────────────────────────────────────────────────────────────────
export const ImageResolution: React.FC<{ data: Extract<ImageBeat, { kind: "resolution" }>; durationFrames: number }> = ({ data, durationFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame: frame - 8, fps, config: { damping: 20 } });

  return (
    <AbsoluteFill>
      <HeroImage imageUrl={data.imageUrl} motionClip={data.motionClip || data.motion_clip} durationFrames={durationFrames} tone="cool" />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center" }}>
        <div style={{
          color: TOKENS.gold, fontFamily: TOKENS.mono, fontSize: 16,
          letterSpacing: "0.4em", opacity: titleEnter,
          textShadow: "0 1px 6px rgba(0,0,0,0.9)",
        }}>
          {data.eyebrow}
        </div>
        <div style={{
          color: TOKENS.ink, fontSize: 52, fontWeight: 700, marginTop: 12, marginBottom: 50,
          letterSpacing: "-0.025em", fontFamily: TOKENS.display,
          opacity: titleEnter, textShadow: "0 2px 14px rgba(0,0,0,0.9)", textAlign: "center", maxWidth: 1400,
        }}>
          {data.headline}
        </div>
        <div style={{
          color: TOKENS.gold, fontSize: 240, fontWeight: 800, fontFamily: TOKENS.display,
          fontVariantNumeric: "tabular-nums", letterSpacing: "-0.04em",
          textShadow: "0 6px 40px rgba(0,0,0,0.85), 0 0 80px rgba(250,204,21,0.4)",
        }}>
          {data.bigStat}
        </div>
        <div style={{
          color: TOKENS.ink, fontSize: 28, fontWeight: 500, marginTop: 16, fontFamily: TOKENS.display,
          textShadow: "0 1px 10px rgba(0,0,0,0.85)",
        }}>
          {data.bigStatLabel}
        </div>
        <div style={{ display: "flex", gap: 60, marginTop: 56 }}>
          {data.stats.map((s, i) => {
            const e = spring({ frame: frame - (90 + i * 15), fps, config: { damping: 18 } });
            return (
              <div key={s.l} style={{ textAlign: "center", opacity: e }}>
                <div style={{ color: TOKENS.ink, fontSize: 40, fontWeight: 700, fontVariantNumeric: "tabular-nums", textShadow: "0 1px 8px rgba(0,0,0,0.85)" }}>{s.v}</div>
                <div style={{ color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 12, letterSpacing: "0.15em", marginTop: 4 }}>{s.l}</div>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 6 · IMAGE-CTA — clean call-to-action on closing image
// ────────────────────────────────────────────────────────────────────────────
export const ImageCTA: React.FC<{ data: Extract<ImageBeat, { kind: "cta" }>; durationFrames: number }> = ({ data, durationFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - 4, fps, config: { damping: 18, stiffness: 120 } });
  const urlEnter = spring({ frame: frame - 22, fps, config: { damping: 16 } });

  return (
    <AbsoluteFill>
      <HeroImage imageUrl={data.imageUrl} motionClip={data.motionClip || data.motion_clip} durationFrames={durationFrames} tone="warm" />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center" }}>
        <div style={{
          color: TOKENS.gold, fontFamily: TOKENS.mono, fontSize: 18,
          letterSpacing: "0.4em", opacity: enter, marginBottom: 36,
          textShadow: "0 1px 8px rgba(0,0,0,0.9)",
        }}>
          {data.eyebrow}
        </div>
        <div style={{
          color: TOKENS.ink, fontSize: 80, fontWeight: 800,
          letterSpacing: "-0.03em", fontFamily: TOKENS.display, textAlign: "center",
          opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [30, 0])}px)`,
          textShadow: "0 4px 24px rgba(0,0,0,0.9)",
        }}>
          {data.headline}
        </div>
        <div style={{
          marginTop: 52, padding: "18px 48px",
          border: `1.5px solid ${TOKENS.gold}`, borderRadius: 999,
          color: TOKENS.gold, fontSize: 26, fontFamily: TOKENS.mono, letterSpacing: "0.05em",
          background: "rgba(10,10,12,0.55)", backdropFilter: "blur(8px)",
          opacity: urlEnter, transform: `translateY(${interpolate(urlEnter, [0, 1], [20, 0])}px)`,
          boxShadow: "0 0 40px rgba(250,204,21,0.25)",
        }}>
          {data.url}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// Router
// ────────────────────────────────────────────────────────────────────────────
export const ImageBeatRouter: React.FC<{ beat: ImageBeat; durationFrames: number }> = ({ beat, durationFrames }) => {
  switch (beat.kind) {
    case "hook":         return <ImageHook data={beat} durationFrames={durationFrames} />;
    case "setup":        return <ImageSetup data={beat} durationFrames={durationFrames} />;
    case "conflict":     return <ImageConflict data={beat} durationFrames={durationFrames} />;
    case "breakthrough": return <ImageBreakthrough data={beat} durationFrames={durationFrames} />;
    case "resolution":   return <ImageResolution data={beat} durationFrames={durationFrames} />;
    case "cta":          return <ImageCTA data={beat} durationFrames={durationFrames} />;
  }
};
