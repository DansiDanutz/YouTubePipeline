import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// Evidence 2 — "Data center capacity: 1.9 GW for OpenAI 2025, energy contracts"
// Five glowing power-stat cards
// 6.5s @ 30fps = 195 frames
const STATS = [
  { label: "OPENAI · STARGATE", value: "1.9 GW", caption: "+200% YoY" },
  { label: "MICROSOFT", value: "1.4 GW", caption: "Azure AI fleet" },
  { label: "GOOGLE TPU", value: "0.9 GW", caption: "Iowa + Oklahoma" },
  { label: "ANTHROPIC", value: "0.4 GW", caption: "via AWS Trainium" },
  { label: "META AI", value: "1.1 GW", caption: "Llama clusters" },
];

export const Scene04DataCenters: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });

  return (
    <AbsoluteFill style={{ background: "#05070a", fontFamily: "Inter, system-ui, sans-serif" }}>
      <div
        style={{
          position: "absolute",
          top: 100,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#64748b",
          fontSize: 18,
          letterSpacing: "0.4em",
          fontFamily: "JetBrains Mono, monospace",
          opacity: titleEnter,
        }}
      >
        EVIDENCE · DATA CENTER CAPACITY
      </div>

      <div
        style={{
          position: "absolute",
          top: 155,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#f1f5f9",
          fontSize: 56,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          opacity: titleEnter,
        }}
      >
        Power is the new perimeter.
      </div>

      {/* Cards row */}
      <div
        style={{
          position: "absolute",
          top: 360,
          left: 80,
          right: 80,
          display: "flex",
          gap: 24,
          justifyContent: "center",
        }}
      >
        {STATS.map((s, i) => {
          const delay = 18 + i * 10;
          const cardEnter = spring({ frame: frame - delay, fps, config: { damping: 16, stiffness: 100 } });
          return (
            <div
              key={s.label}
              style={{
                width: 320,
                height: 380,
                border: "1px solid rgba(16, 185, 129, 0.25)",
                background:
                  "linear-gradient(180deg, rgba(16,185,129,0.06) 0%, rgba(16,185,129,0.01) 100%)",
                borderRadius: 6,
                padding: "36px 28px",
                opacity: cardEnter,
                transform: `translateY(${interpolate(cardEnter, [0, 1], [40, 0])}px)`,
                boxShadow: "0 0 40px rgba(16, 185, 129, 0.06)",
              }}
            >
              <div
                style={{
                  color: "#10b981",
                  fontFamily: "JetBrains Mono, monospace",
                  fontSize: 13,
                  letterSpacing: "0.25em",
                  marginBottom: 32,
                  minHeight: 36,
                }}
              >
                {s.label}
              </div>
              <div
                style={{
                  color: "#f1f5f9",
                  fontSize: 76,
                  fontWeight: 800,
                  lineHeight: 1,
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.03em",
                }}
              >
                {s.value}
              </div>
              <div
                style={{
                  color: "#94a3b8",
                  fontSize: 18,
                  marginTop: 18,
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >
                {s.caption}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
