import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// ────────────────────────────────────────────────────────────────────────────
// Data-driven story scenes for ANY topic.
// Pass a `manifest` prop with 6 beats; the same components render any story.
// ────────────────────────────────────────────────────────────────────────────

export const TOKENS = {
  bg: "#0a0a0c",
  ink: "#f5f5f0",
  muted: "#8a8a8a",
  rule: "rgba(245,245,240,0.08)",
  gold: "#facc15",
  warm: "#f59e0b",
  danger: "#ef4444",
  ok: "#10b981",
  display: "Inter, -apple-system, sans-serif",
  mono: "JetBrains Mono, ui-monospace, monospace",
} as const;

// ────────────────────────────────────────────────────────────────────────────
// EFFECTS LIBRARY — reusable cinematic primitives
// ────────────────────────────────────────────────────────────────────────────

/** Soft vignette + grain overlay — gives every scene a film feel */
export const FilmGrade: React.FC = () => {
  const frame = useCurrentFrame();
  const flicker = 0.92 + 0.08 * Math.sin(frame * 0.4);
  return (
    <>
      {/* Vignette */}
      <AbsoluteFill style={{
        background: "radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.55) 100%)",
        pointerEvents: "none",
      }}/>
      {/* Subtle flicker on a tinted overlay */}
      <AbsoluteFill style={{
        background: `rgba(250,204,21,${0.012 * flicker})`,
        mixBlendMode: "overlay",
        pointerEvents: "none",
      }}/>
    </>
  );
};

/** Subtle background grid — present across scenes for visual continuity */
export const GridBG: React.FC<{ opacity?: number; color?: string }> = ({ opacity = 0.05, color = TOKENS.gold }) => (
  <svg width="1920" height="1080" style={{ position: "absolute", inset: 0, opacity }}>
    <defs>
      <pattern id="grid" width="80" height="80" patternUnits="userSpaceOnUse">
        <path d="M 80 0 L 0 0 0 80" fill="none" stroke={color} strokeWidth="0.5" />
      </pattern>
    </defs>
    <rect width="1920" height="1080" fill="url(#grid)" />
  </svg>
);

/** Sweep reveal — animates a colored bar that wipes across, then content fades in behind */
export const SweepReveal: React.FC<{ from?: number; children: React.ReactNode }> = ({ from = 5, children }) => {
  const frame = useCurrentFrame();
  const reveal = interpolate(frame - from, [0, 18, 26], [0, 1, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - Math.pow(1 - t, 3),
  });
  return (
    <div style={{ position: "relative" }}>
      <div style={{ opacity: reveal, transform: `translateY(${(1 - reveal) * 14}px)` }}>{children}</div>
    </div>
  );
};

/** Animated counter — counts up from 0 to target with cubic ease-out */
export function useCounter(target: number, fromFrame = 25, durationFrames = 90) {
  const frame = useCurrentFrame();
  return Math.floor(interpolate(frame, [fromFrame, fromFrame + durationFrames], [0, target], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: (t) => 1 - Math.pow(1 - t, 4),
  }));
}

// ────────────────────────────────────────────────────────────────────────────
// BEAT TYPES — the six narrative beat shapes
// ────────────────────────────────────────────────────────────────────────────

export type Beat =
  | { kind: "hook"; eyebrow: string; statValue: number; statSuffix?: string; statPrefix?: string; label: string; subLabel?: string }
  | { kind: "setup"; eyebrow: string; mark: string; name: string; subtitle: string; tags: string[] }
  | { kind: "conflict"; eyebrow: string; headline: string; bars: { label: string; value: number; unit: string; color?: string }[]; total: { label: string; value: string } }
  | { kind: "breakthrough"; eyebrow: string; headline: string; rows: { rank: number; name: string; value: string; lang?: string; hero?: boolean }[] }
  | { kind: "resolution"; eyebrow: string; headline: string; bigStat: string; bigStatLabel: string; stats: { v: string; l: string }[] }
  | { kind: "cta"; eyebrow: string; headline: string; url: string };

// ────────────────────────────────────────────────────────────────────────────
// 1 · HOOK — counter race + tagline
// ────────────────────────────────────────────────────────────────────────────
export const Hook: React.FC<{ data: Extract<Beat, { kind: "hook" }> }> = ({ data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - 8, fps, config: { damping: 18, stiffness: 90 } });
  const value = useCounter(data.statValue, 25, 105);
  const labelOp = interpolate(frame, [110, 140], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const yearOp = interpolate(frame, [10, 35], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const formatted = value.toLocaleString();
  return (
    <AbsoluteFill style={{ background: TOKENS.bg, fontFamily: TOKENS.display }}>
      <GridBG />
      <div style={{ position: "absolute", top: 220, left: 0, right: 0, textAlign: "center", color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 18, letterSpacing: "0.4em", opacity: yearOp }}>
        {data.eyebrow}
      </div>
      {data.subLabel && (
        <div style={{ position: "absolute", top: 290, left: 0, right: 0, textAlign: "center", color: TOKENS.muted, fontSize: 28, fontWeight: 500, opacity: yearOp }}>
          {data.subLabel}
        </div>
      )}
      <div style={{
        position: "absolute", top: 380, left: 0, right: 0, textAlign: "center", color: TOKENS.gold,
        fontSize: 240, fontWeight: 800, fontFeatureSettings: '"tnum"', fontVariantNumeric: "tabular-nums",
        letterSpacing: "-0.04em", textShadow: "0 0 60px rgba(250, 204, 21, 0.4)",
        opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [40, 0])}px)`,
      }}>
        {data.statPrefix || ""}{formatted}{data.statSuffix || ""}
      </div>
      <div style={{ position: "absolute", top: 700, left: 0, right: 0, textAlign: "center", color: TOKENS.ink, fontSize: 36, fontWeight: 600, opacity: labelOp }}>
        {data.label}
      </div>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 2 · SETUP — protagonist card with mark + tags
// ────────────────────────────────────────────────────────────────────────────
export const Setup: React.FC<{ data: Extract<Beat, { kind: "setup" }> }> = ({ data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const nameEnter = spring({ frame: frame - 30, fps, config: { damping: 18 } });
  const tagsEnter = interpolate(frame, [60, 100], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ background: TOKENS.bg, fontFamily: TOKENS.display }}>
      <GridBG opacity={0.04} color={TOKENS.muted} />
      <div style={{ position: "absolute", top: 130, left: 0, right: 0, textAlign: "center", color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 18, letterSpacing: "0.4em", opacity: enter }}>
        {data.eyebrow}
      </div>
      <div style={{
        position: "absolute", top: 280, left: 880, width: 160, height: 160, border: `2px solid ${TOKENS.ink}`, borderRadius: "50%",
        display: "flex", alignItems: "center", justifyContent: "center", color: TOKENS.ink, fontSize: 80, fontFamily: TOKENS.mono,
        opacity: enter,
      }}>
        {data.mark}
      </div>
      <div style={{
        position: "absolute", top: 480, left: 0, right: 0, textAlign: "center", color: TOKENS.ink, fontSize: 76, fontWeight: 700,
        letterSpacing: "-0.025em", opacity: nameEnter, transform: `translateY(${interpolate(nameEnter, [0, 1], [20, 0])}px)`,
      }}>
        {data.name}
      </div>
      <div style={{ position: "absolute", top: 580, left: 0, right: 0, textAlign: "center", color: TOKENS.muted, fontSize: 28, fontWeight: 400, opacity: nameEnter }}>
        {data.subtitle}
      </div>
      <div style={{ position: "absolute", top: 720, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 40, opacity: tagsEnter }}>
        {data.tags.map((t) => (
          <div key={t} style={{ padding: "12px 28px", border: `1px solid ${TOKENS.rule}`, borderRadius: 4, color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 18, letterSpacing: "0.15em" }}>
            {t}
          </div>
        ))}
      </div>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 3 · CONFLICT — bar stack + total + diagonal slash
// ────────────────────────────────────────────────────────────────────────────
export const Conflict: React.FC<{ data: Extract<Beat, { kind: "conflict" }> }> = ({ data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const totalEnter = interpolate(frame, [180, 220], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const slashEnter = interpolate(frame, [200, 230], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const maxBarValue = Math.max(...data.bars.map(b => b.value));
  return (
    <AbsoluteFill style={{ background: TOKENS.bg, fontFamily: TOKENS.display }}>
      <div style={{ position: "absolute", top: 100, left: 80, color: TOKENS.danger, fontFamily: TOKENS.mono, fontSize: 18, letterSpacing: "0.4em", opacity: titleEnter }}>
        {data.eyebrow}
      </div>
      <div style={{ position: "absolute", top: 145, left: 80, right: 80, color: TOKENS.ink, fontSize: 64, fontWeight: 700, letterSpacing: "-0.025em", opacity: titleEnter }}>
        {data.headline}
      </div>
      <div style={{ position: "absolute", top: 320, left: 80, right: 80 }}>
        {data.bars.map((b, i) => {
          const barEnter = spring({ frame: frame - (25 + i * 18), fps, config: { damping: 14, stiffness: 70 } });
          const w = (b.value / maxBarValue) * 90;
          const c = b.color || (i % 2 === 0 ? TOKENS.gold : TOKENS.warm);
          return (
            <div key={b.label} style={{ marginBottom: 32 }}>
              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 8 }}>
                <span style={{ color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 18, width: 280, letterSpacing: "0.15em" }}>{b.label}</span>
                <span style={{ color: c, fontSize: 32, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {b.value}{b.unit}
                </span>
              </div>
              <div style={{ height: 48, width: `${barEnter * w}%`, background: `linear-gradient(90deg, ${c} 0%, ${c}cc 100%)`, borderRadius: 2 }} />
            </div>
          );
        })}
      </div>
      <div style={{ position: "absolute", top: 880, left: 80, color: TOKENS.danger, fontFamily: TOKENS.mono, fontSize: 22, letterSpacing: "0.1em", opacity: totalEnter }}>
        {data.total.label} · {data.total.value}
      </div>
      <svg width="1920" height="1080" style={{ position: "absolute", inset: 0, pointerEvents: "none", opacity: slashEnter }}>
        <line x1="80" y1="850" x2="1840" y2="320" stroke={TOKENS.danger} strokeWidth="6" strokeDasharray="20 16" />
      </svg>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 4 · BREAKTHROUGH — leaderboard with hero row glow
// ────────────────────────────────────────────────────────────────────────────
export const Breakthrough: React.FC<{ data: Extract<Beat, { kind: "breakthrough" }> }> = ({ data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  return (
    <AbsoluteFill style={{ background: TOKENS.bg, fontFamily: TOKENS.display }}>
      <GridBG opacity={0.04} />
      <div style={{ position: "absolute", top: 80, left: 80, color: TOKENS.gold, fontFamily: TOKENS.mono, fontSize: 18, letterSpacing: "0.4em", opacity: titleEnter }}>
        {data.eyebrow}
      </div>
      <div style={{ position: "absolute", top: 125, left: 80, right: 80, color: TOKENS.ink, fontSize: 60, fontWeight: 700, letterSpacing: "-0.025em", opacity: titleEnter }}>
        {data.headline}
      </div>
      <div style={{ position: "absolute", top: 280, left: 80, right: 80 }}>
        {data.rows.map((r, i) => {
          const rowEnter = spring({ frame: frame - (35 + i * 25), fps, config: { damping: 16, stiffness: 90 } });
          const isHero = r.hero;
          return (
            <div key={r.rank} style={{
              display: "flex", alignItems: "center",
              padding: isHero ? "22px 32px" : "16px 32px",
              borderTop: i === 0 ? `1px solid ${TOKENS.rule}` : "none",
              borderBottom: `1px solid ${TOKENS.rule}`,
              background: isHero ? "rgba(250, 204, 21, 0.06)" : "transparent",
              opacity: rowEnter, transform: `translateX(${interpolate(rowEnter, [0, 1], [-30, 0])}px)`,
            }}>
              <div style={{ width: 60, color: isHero ? TOKENS.gold : TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 22, fontWeight: 600 }}>#{r.rank}</div>
              <div style={{ flex: 1, color: isHero ? TOKENS.ink : TOKENS.muted, fontFamily: TOKENS.mono, fontSize: isHero ? 28 : 22, fontWeight: isHero ? 600 : 400 }}>{r.name}</div>
              {r.lang && <div style={{ width: 110, color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 16, textAlign: "right" }}>{r.lang}</div>}
              <div style={{
                width: 230, textAlign: "right", color: isHero ? TOKENS.gold : TOKENS.ink,
                fontSize: isHero ? 56 : 36, fontWeight: 700, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em",
              }}>
                {r.value}
              </div>
            </div>
          );
        })}
      </div>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 5 · RESOLUTION — big stat + 3 supporting stats
// ────────────────────────────────────────────────────────────────────────────
export const Resolution: React.FC<{ data: Extract<Beat, { kind: "resolution" }> }> = ({ data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  return (
    <AbsoluteFill style={{ background: TOKENS.bg, fontFamily: TOKENS.display }}>
      <GridBG opacity={0.06} />
      <div style={{ position: "absolute", top: 130, left: 0, right: 0, textAlign: "center", color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 18, letterSpacing: "0.4em", opacity: titleEnter }}>
        {data.eyebrow}
      </div>
      <div style={{ position: "absolute", top: 195, left: 0, right: 0, textAlign: "center", color: TOKENS.ink, fontSize: 60, fontWeight: 700, letterSpacing: "-0.025em", opacity: titleEnter }}>
        {data.headline}
      </div>
      <div style={{
        position: "absolute", top: 380, left: 0, right: 0, textAlign: "center", color: TOKENS.gold,
        fontSize: 280, fontWeight: 800, fontFeatureSettings: '"tnum"', fontVariantNumeric: "tabular-nums",
        letterSpacing: "-0.04em", textShadow: "0 0 60px rgba(250, 204, 21, 0.4)",
      }}>
        {data.bigStat}
      </div>
      <div style={{ position: "absolute", top: 740, left: 0, right: 0, textAlign: "center", color: TOKENS.ink, fontSize: 32, fontWeight: 500 }}>
        {data.bigStatLabel}
      </div>
      <div style={{ position: "absolute", top: 830, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 80 }}>
        {data.stats.map((s, i) => {
          const e = spring({ frame: frame - (90 + i * 15), fps, config: { damping: 18 } });
          return (
            <div key={s.l} style={{ textAlign: "center", opacity: e }}>
              <div style={{ color: TOKENS.ink, fontSize: 46, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{s.v}</div>
              <div style={{ color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 14, letterSpacing: "0.15em", marginTop: 6 }}>{s.l}</div>
            </div>
          );
        })}
      </div>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// 6 · CTA — call to action with URL pill
// ────────────────────────────────────────────────────────────────────────────
export const CTA: React.FC<{ data: Extract<Beat, { kind: "cta" }> }> = ({ data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - 4, fps, config: { damping: 18, stiffness: 120 } });
  const urlEnter = spring({ frame: frame - 22, fps, config: { damping: 16 } });
  return (
    <AbsoluteFill style={{ background: TOKENS.bg, fontFamily: TOKENS.display }}>
      <div style={{ position: "absolute", top: 360, left: 0, right: 0, textAlign: "center", color: TOKENS.muted, fontFamily: TOKENS.mono, fontSize: 20, letterSpacing: "0.4em", opacity: enter }}>
        {data.eyebrow}
      </div>
      <div style={{
        position: "absolute", top: 430, left: 0, right: 0, textAlign: "center", color: TOKENS.ink, fontSize: 88, fontWeight: 800,
        letterSpacing: "-0.03em", opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [30, 0])}px)`,
      }}>
        {data.headline}
      </div>
      <div style={{
        position: "absolute", top: 620, left: 0, right: 0, display: "flex", justifyContent: "center",
        opacity: urlEnter, transform: `translateY(${interpolate(urlEnter, [0, 1], [20, 0])}px)`,
      }}>
        <div style={{
          padding: "20px 56px", border: `1.5px solid ${TOKENS.gold}`, borderRadius: 999, color: TOKENS.gold,
          fontSize: 32, fontFamily: TOKENS.mono, letterSpacing: "0.05em", background: "rgba(250, 204, 21, 0.06)",
        }}>
          {data.url}
        </div>
      </div>
      <FilmGrade />
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// BeatRouter — picks the right component based on beat.kind
// ────────────────────────────────────────────────────────────────────────────
export const BeatRouter: React.FC<{ beat: Beat }> = ({ beat }) => {
  switch (beat.kind) {
    case "hook":         return <Hook data={beat} />;
    case "setup":        return <Setup data={beat} />;
    case "conflict":     return <Conflict data={beat} />;
    case "breakthrough": return <Breakthrough data={beat} />;
    case "resolution":   return <Resolution data={beat} />;
    case "cta":          return <CTA data={beat} />;
  }
};
