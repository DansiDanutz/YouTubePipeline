import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// Hook — "NVIDIA shares surged ~1000% from the first AI compute squeeze"
// Editorial dark, single accent (electric green), tabular numbers.
// 7s @ 30fps = 210 frames
export const Scene01Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame: frame - 8, fps, config: { damping: 18, stiffness: 80 } });
  const counter = Math.floor(interpolate(frame, [20, 110], [0, 974], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - Math.pow(1 - t, 3),
  }));
  const labelOpacity = interpolate(frame, [110, 130], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: "#05070a", fontFamily: "Inter, system-ui, sans-serif" }}>
      {/* Background grid texture (subtle) */}
      <svg width="1920" height="1080" style={{ position: "absolute", inset: 0, opacity: 0.06 }}>
        <defs>
          <pattern id="grid" width="80" height="80" patternUnits="userSpaceOnUse">
            <path d="M 80 0 L 0 0 0 80" fill="none" stroke="#10b981" strokeWidth="0.6" />
          </pattern>
        </defs>
        <rect width="1920" height="1080" fill="url(#grid)" />
      </svg>

      {/* Eyebrow */}
      <div
        style={{
          position: "absolute",
          top: 280,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#10b981",
          fontSize: 22,
          letterSpacing: "0.4em",
          opacity: enter,
          fontFamily: "JetBrains Mono, monospace",
        }}
      >
        AI COMPUTE GOLD RUSH · MAY 2026
      </div>

      {/* Main number */}
      <div
        style={{
          position: "absolute",
          top: 380,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#10b981",
          fontSize: 280,
          fontWeight: 800,
          fontFeatureSettings: '"tnum"',
          fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.04em",
          textShadow: "0 0 40px rgba(16, 185, 129, 0.35)",
          transform: `translateY(${interpolate(enter, [0, 1], [40, 0])}px)`,
          opacity: enter,
        }}
      >
        +{counter}%
      </div>

      {/* Sub-label */}
      <div
        style={{
          position: "absolute",
          top: 740,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#94a3b8",
          fontSize: 36,
          fontWeight: 500,
          opacity: labelOpacity,
        }}
      >
        NVIDIA market cap, since the first compute crunch
      </div>

      {/* Citation badge */}
      <div
        style={{
          position: "absolute",
          top: 800,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#475569",
          fontSize: 18,
          fontFamily: "JetBrains Mono, monospace",
          letterSpacing: "0.1em",
          opacity: labelOpacity,
        }}
      >
        SOURCE: NASDAQ · 2024–2026
      </div>
    </AbsoluteFill>
  );
};
