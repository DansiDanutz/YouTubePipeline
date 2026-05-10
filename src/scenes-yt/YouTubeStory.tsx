import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// ────────────────────────────────────────────────────────────────────────────
// SHARED DESIGN TOKENS — locked in steps/03_design_system.json
// huashu §1: single accent (gold for "gold rush"). ui-ux-pro-max §6: tabular nums.
// ────────────────────────────────────────────────────────────────────────────
const T = {
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
};

// Background grid texture — used across scenes
const GridBG: React.FC<{ opacity?: number; color?: string }> = ({ opacity = 0.05, color = T.gold }) => (
  <svg width="1920" height="1080" style={{ position: "absolute", inset: 0, opacity }}>
    <defs>
      <pattern id="g" width="80" height="80" patternUnits="userSpaceOnUse">
        <path d="M 80 0 L 0 0 0 80" fill="none" stroke={color} strokeWidth="0.5" />
      </pattern>
    </defs>
    <rect width="1920" height="1080" fill="url(#g)" />
  </svg>
);

// ────────────────────────────────────────────────────────────────────────────
// SCENE 1 · HOOK — 30K stars counter (0–7s · 210 frames)
// ────────────────────────────────────────────────────────────────────────────
export const Scene1Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame: frame - 8, fps, config: { damping: 18, stiffness: 90 } });
  const counter = Math.floor(
    interpolate(frame, [25, 130], [0, 30346], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: (t) => 1 - Math.pow(1 - t, 4),
    })
  );
  const labelOp = interpolate(frame, [110, 140], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const yearOp = interpolate(frame, [10, 35], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: T.bg, fontFamily: T.display }}>
      <GridBG />

      <div style={{ position: "absolute", top: 220, left: 0, right: 0, textAlign: "center", color: T.muted, fontFamily: T.mono, fontSize: 18, letterSpacing: "0.4em", opacity: yearOp }}>
        2023 · LATE OCTOBER
      </div>

      <div style={{ position: "absolute", top: 290, left: 0, right: 0, textAlign: "center", color: T.muted, fontSize: 28, fontWeight: 500, opacity: yearOp }}>
        One GitHub repository.
      </div>

      <div
        style={{
          position: "absolute",
          top: 380,
          left: 0,
          right: 0,
          textAlign: "center",
          color: T.gold,
          fontSize: 240,
          fontWeight: 800,
          fontFeatureSettings: '"tnum"',
          fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.04em",
          textShadow: "0 0 60px rgba(250, 204, 21, 0.4)",
          opacity: enter,
          transform: `translateY(${interpolate(enter, [0, 1], [40, 0])}px)`,
        }}
      >
        {counter.toLocaleString()}
      </div>

      <div style={{ position: "absolute", top: 700, left: 0, right: 0, textAlign: "center", color: T.ink, fontSize: 36, fontWeight: 600, opacity: labelOp }}>
        ★ stars · in days
      </div>

      <div style={{ position: "absolute", top: 770, left: 0, right: 0, textAlign: "center", color: T.muted, fontFamily: T.mono, fontSize: 18, letterSpacing: "0.15em", opacity: labelOp }}>
        FujiwaraChoki/MoneyPrinter
      </div>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// SCENE 2 · SETUP — The lone dev (7–13.5s · 195 frames)
// ────────────────────────────────────────────────────────────────────────────
export const Scene2Setup: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const nameEnter = spring({ frame: frame - 30, fps, config: { damping: 18 } });
  const tagsEnter = interpolate(frame, [60, 100], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: T.bg, fontFamily: T.display }}>
      <GridBG opacity={0.04} color={T.muted} />

      <div style={{ position: "absolute", top: 130, left: 0, right: 0, textAlign: "center", color: T.muted, fontFamily: T.mono, fontSize: 18, letterSpacing: "0.4em", opacity: enter }}>
        BEFORE THE BOOM · LATE 2023
      </div>

      {/* Anonymous coder mark — abstract not figurative */}
      <div
        style={{
          position: "absolute",
          top: 280,
          left: 880,
          width: 160,
          height: 160,
          border: `2px solid ${T.ink}`,
          borderRadius: "50%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: T.ink,
          fontSize: 80,
          fontFamily: T.mono,
          opacity: enter,
        }}
      >
        ?
      </div>

      <div style={{ position: "absolute", top: 480, left: 0, right: 0, textAlign: "center", color: T.ink, fontSize: 76, fontWeight: 700, letterSpacing: "-0.025em", opacity: nameEnter, transform: `translateY(${interpolate(nameEnter, [0, 1], [20, 0])}px)` }}>
        FujiwaraChoki
      </div>

      <div style={{ position: "absolute", top: 580, left: 0, right: 0, textAlign: "center", color: T.muted, fontSize: 28, fontWeight: 400, opacity: nameEnter }}>
        anonymous developer
      </div>

      <div style={{ position: "absolute", top: 720, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 40, opacity: tagsEnter }}>
        {["Python", "MoviePy", "OpenAI"].map((tag) => (
          <div
            key={tag}
            style={{
              padding: "12px 28px",
              border: `1px solid ${T.rule}`,
              borderRadius: 4,
              color: T.muted,
              fontFamily: T.mono,
              fontSize: 18,
              letterSpacing: "0.15em",
            }}
          >
            {tag}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// SCENE 3 · CONFLICT — Hours-per-video grind (13.5–21.5s · 240 frames)
// ────────────────────────────────────────────────────────────────────────────
const GRIND = [
  { label: "SCRIPTING",   hours: 8,  color: T.gold },
  { label: "EDITING",     hours: 12, color: T.warm },
  { label: "THUMBNAILS",  hours: 4,  color: T.gold },
  { label: "UPLOAD + SEO",hours: 2,  color: T.warm },
];
const TOTAL_HOURS = GRIND.reduce((s, g) => s + g.hours, 0);

export const Scene3Conflict: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const totalEnter = interpolate(frame, [180, 220], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const slashEnter = interpolate(frame, [200, 230], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: T.bg, fontFamily: T.display }}>
      <div style={{ position: "absolute", top: 100, left: 80, color: T.danger, fontFamily: T.mono, fontSize: 18, letterSpacing: "0.4em", opacity: titleEnter }}>
        BURNOUT · 26 HOURS PER VIDEO
      </div>

      <div style={{ position: "absolute", top: 145, left: 80, right: 80, color: T.ink, fontSize: 64, fontWeight: 700, letterSpacing: "-0.025em", opacity: titleEnter }}>
        The grind broke creators.
      </div>

      {/* Bar stack */}
      <div style={{ position: "absolute", top: 320, left: 80, right: 80 }}>
        {GRIND.map((g, i) => {
          const barEnter = spring({ frame: frame - (25 + i * 18), fps, config: { damping: 14, stiffness: 70 } });
          const w = (g.hours / 12) * 90;
          return (
            <div key={g.label} style={{ marginBottom: 36 }}>
              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 8 }}>
                <span style={{ color: T.muted, fontFamily: T.mono, fontSize: 18, width: 280, letterSpacing: "0.15em" }}>{g.label}</span>
                <span style={{ color: g.color, fontSize: 32, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                  {g.hours}h
                </span>
              </div>
              <div
                style={{
                  height: 48,
                  width: `${barEnter * w}%`,
                  background: `linear-gradient(90deg, ${g.color} 0%, ${g.color}cc 100%)`,
                  borderRadius: 2,
                }}
              />
            </div>
          );
        })}
      </div>

      {/* Total + diagonal slash */}
      <div style={{ position: "absolute", top: 880, left: 80, color: T.danger, fontFamily: T.mono, fontSize: 22, letterSpacing: "0.1em", opacity: totalEnter }}>
        TOTAL · {TOTAL_HOURS}h × every single video
      </div>

      <svg
        width="1920"
        height="1080"
        style={{ position: "absolute", inset: 0, pointerEvents: "none", opacity: slashEnter }}
      >
        <line x1="80" y1="850" x2="1840" y2="320" stroke={T.danger} strokeWidth="6" strokeDasharray="20 16" />
      </svg>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// SCENE 4 · BREAKTHROUGH — OSS leaderboard (21.5–30s · 255 frames)
// ────────────────────────────────────────────────────────────────────────────
const REPOS = [
  { rank: 1, name: "FujiwaraChoki/MoneyPrinterV2",     stars: 30346, lang: "Python",      hero: true },
  { rank: 2, name: "FujiwaraChoki/MoneyPrinter",       stars: 13158, lang: "Python",      hero: false },
  { rank: 3, name: "RayVentura/ShortGPT",              stars: 7303,  lang: "Python",      hero: false },
  { rank: 4, name: "SamurAIGPT/AI-Youtube-Shorts-Generator", stars: 3378, lang: "Python", hero: false },
  { rank: 5, name: "gyoridavid/short-video-maker",     stars: 1110,  lang: "TypeScript",  hero: false },
];

export const Scene4Breakthrough: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });

  return (
    <AbsoluteFill style={{ background: T.bg, fontFamily: T.display }}>
      <GridBG opacity={0.04} />

      <div style={{ position: "absolute", top: 80, left: 80, color: T.gold, fontFamily: T.mono, fontSize: 18, letterSpacing: "0.4em", opacity: titleEnter }}>
        THE OPEN-SOURCE CASCADE · 2023 → 2026
      </div>
      <div style={{ position: "absolute", top: 125, left: 80, right: 80, color: T.ink, fontSize: 60, fontWeight: 700, letterSpacing: "-0.025em", opacity: titleEnter }}>
        MoneyPrinter ships → the floodgates open.
      </div>

      {/* Leaderboard */}
      <div style={{ position: "absolute", top: 280, left: 80, right: 80 }}>
        {REPOS.map((r, i) => {
          const rowEnter = spring({ frame: frame - (35 + i * 25), fps, config: { damping: 16, stiffness: 90 } });
          const isHero = r.hero;
          return (
            <div
              key={r.rank}
              style={{
                display: "flex",
                alignItems: "center",
                padding: isHero ? "22px 32px" : "16px 32px",
                borderTop: i === 0 ? `1px solid ${T.rule}` : "none",
                borderBottom: `1px solid ${T.rule}`,
                background: isHero ? "rgba(250, 204, 21, 0.06)" : "transparent",
                opacity: rowEnter,
                transform: `translateX(${interpolate(rowEnter, [0, 1], [-30, 0])}px)`,
              }}
            >
              <div style={{ width: 60, color: isHero ? T.gold : T.muted, fontFamily: T.mono, fontSize: 22, fontWeight: 600 }}>#{r.rank}</div>
              <div style={{ flex: 1, color: isHero ? T.ink : T.muted, fontFamily: T.mono, fontSize: isHero ? 28 : 22, fontWeight: isHero ? 600 : 400 }}>{r.name}</div>
              <div style={{ width: 110, color: T.muted, fontFamily: T.mono, fontSize: 16, textAlign: "right" }}>{r.lang}</div>
              <div
                style={{
                  width: 230,
                  textAlign: "right",
                  color: isHero ? T.gold : T.ink,
                  fontSize: isHero ? 56 : 36,
                  fontWeight: 700,
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.02em",
                }}
              >
                {r.stars.toLocaleString()} ★
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// SCENE 5 · RESOLUTION — Faceless YouTube as a category (30–37s · 210 frames)
// ────────────────────────────────────────────────────────────────────────────
export const Scene5Resolution: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const market = Math.floor(
    interpolate(frame, [40, 130], [0, 8.3 * 100], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: (t) => 1 - Math.pow(1 - t, 3),
    })
  ) / 100;

  return (
    <AbsoluteFill style={{ background: T.bg, fontFamily: T.display }}>
      <GridBG opacity={0.06} />

      <div style={{ position: "absolute", top: 130, left: 0, right: 0, textAlign: "center", color: T.muted, fontFamily: T.mono, fontSize: 18, letterSpacing: "0.4em", opacity: titleEnter }}>
        A NEW CATEGORY · FACELESS YOUTUBE
      </div>

      <div style={{ position: "absolute", top: 195, left: 0, right: 0, textAlign: "center", color: T.ink, fontSize: 60, fontWeight: 700, letterSpacing: "-0.025em", opacity: titleEnter }}>
        Millions in revenue. No face required.
      </div>

      <div
        style={{
          position: "absolute",
          top: 380,
          left: 0,
          right: 0,
          textAlign: "center",
          color: T.gold,
          fontSize: 280,
          fontWeight: 800,
          fontFeatureSettings: '"tnum"',
          fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.04em",
          textShadow: "0 0 60px rgba(250, 204, 21, 0.4)",
        }}
      >
        ${market.toFixed(1)}B
      </div>

      <div style={{ position: "absolute", top: 740, left: 0, right: 0, textAlign: "center", color: T.ink, fontSize: 32, fontWeight: 500 }}>
        virtual-influencer market in 2026
      </div>

      {/* Stat strip */}
      <div style={{ position: "absolute", top: 830, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 80 }}>
        {[
          { v: "82%", l: "of internet traffic = video" },
          { v: "10×", l: "production speed via AI" },
          { v: "0", l: "face required" },
        ].map((s, i) => {
          const e = spring({ frame: frame - (90 + i * 15), fps, config: { damping: 18 } });
          return (
            <div key={s.l} style={{ textAlign: "center", opacity: e }}>
              <div style={{ color: T.ink, fontSize: 46, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{s.v}</div>
              <div style={{ color: T.muted, fontFamily: T.mono, fontSize: 14, letterSpacing: "0.15em", marginTop: 6 }}>{s.l}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// ────────────────────────────────────────────────────────────────────────────
// SCENE 6 · CTA — Build yours (37–40s · 90 frames)
// ────────────────────────────────────────────────────────────────────────────
export const Scene6CTA: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame: frame - 4, fps, config: { damping: 18, stiffness: 120 } });
  const urlEnter = spring({ frame: frame - 22, fps, config: { damping: 16 } });

  return (
    <AbsoluteFill style={{ background: T.bg, fontFamily: T.display }}>
      <div style={{ position: "absolute", top: 360, left: 0, right: 0, textAlign: "center", color: T.muted, fontFamily: T.mono, fontSize: 20, letterSpacing: "0.4em", opacity: enter }}>
        BUILD YOURS · OPEN SOURCE
      </div>

      <div style={{ position: "absolute", top: 430, left: 0, right: 0, textAlign: "center", color: T.ink, fontSize: 88, fontWeight: 800, letterSpacing: "-0.03em", opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [30, 0])}px)` }}>
        The tools are free.
      </div>

      <div style={{ position: "absolute", top: 620, left: 0, right: 0, display: "flex", justifyContent: "center", opacity: urlEnter, transform: `translateY(${interpolate(urlEnter, [0, 1], [20, 0])}px)` }}>
        <div
          style={{
            padding: "20px 56px",
            border: `1.5px solid ${T.gold}`,
            borderRadius: 999,
            color: T.gold,
            fontSize: 32,
            fontFamily: T.mono,
            letterSpacing: "0.05em",
            background: "rgba(250, 204, 21, 0.06)",
          }}
        >
          github.com/FujiwaraChoki/MoneyPrinterV2
        </div>
      </div>
    </AbsoluteFill>
  );
};
