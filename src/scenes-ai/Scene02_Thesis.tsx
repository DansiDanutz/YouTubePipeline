import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// Thesis — "Compute access is the new business bottleneck for OpenAI vs Anthropic"
// Two-pillar comparison, single accent green for OpenAI, amber for Anthropic
// 6.5s @ 30fps = 195 frames
export const Scene02Thesis: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const headlineEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const oaiSlide = spring({ frame: frame - 25, fps, config: { damping: 18, stiffness: 90 } });
  const antSlide = spring({ frame: frame - 45, fps, config: { damping: 18, stiffness: 90 } });

  return (
    <AbsoluteFill style={{ background: "#05070a", fontFamily: "Inter, system-ui, sans-serif" }}>
      {/* Eyebrow */}
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
          opacity: headlineEnter,
        }}
      >
        THE NEW BOTTLENECK
      </div>

      {/* Headline */}
      <div
        style={{
          position: "absolute",
          top: 165,
          left: 80,
          right: 80,
          textAlign: "center",
          color: "#f1f5f9",
          fontSize: 72,
          fontWeight: 700,
          letterSpacing: "-0.025em",
          lineHeight: 1.1,
          opacity: headlineEnter,
          transform: `translateY(${interpolate(headlineEnter, [0, 1], [20, 0])}px)`,
        }}
      >
        Compute is the new product
      </div>

      {/* Two pillars */}
      <div
        style={{
          position: "absolute",
          top: 420,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          gap: 80,
        }}
      >
        {/* OpenAI pillar */}
        <div
          style={{
            width: 520,
            height: 460,
            border: "1px solid rgba(16, 185, 129, 0.25)",
            background: "linear-gradient(180deg, rgba(16,185,129,0.08) 0%, rgba(16,185,129,0.02) 100%)",
            borderRadius: 4,
            padding: "44px 36px",
            opacity: oaiSlide,
            transform: `translateX(${interpolate(oaiSlide, [0, 1], [-60, 0])}px)`,
          }}
        >
          <div style={{ color: "#10b981", fontFamily: "JetBrains Mono, monospace", fontSize: 14, letterSpacing: "0.3em", marginBottom: 28 }}>
            OPENAI
          </div>
          <div style={{ color: "#f1f5f9", fontSize: 110, fontWeight: 800, lineHeight: 1, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.03em" }}>
            $600B
          </div>
          <div style={{ color: "#10b981", fontSize: 22, fontWeight: 600, marginTop: 12 }}>
            cumulative infra spend by 2030
          </div>
          <div style={{ color: "#64748b", fontSize: 16, marginTop: 28, lineHeight: 1.5, fontFamily: "JetBrains Mono, monospace" }}>
            Strategy: raw scale.<br />
            Stargate · Microsoft · 1.9 GW for 2025
          </div>
        </div>

        {/* Anthropic pillar */}
        <div
          style={{
            width: 520,
            height: 460,
            border: "1px solid rgba(245, 158, 11, 0.25)",
            background: "linear-gradient(180deg, rgba(245,158,11,0.08) 0%, rgba(245,158,11,0.02) 100%)",
            borderRadius: 4,
            padding: "44px 36px",
            opacity: antSlide,
            transform: `translateX(${interpolate(antSlide, [0, 1], [60, 0])}px)`,
          }}
        >
          <div style={{ color: "#f59e0b", fontFamily: "JetBrains Mono, monospace", fontSize: 14, letterSpacing: "0.3em", marginBottom: 28 }}>
            ANTHROPIC
          </div>
          <div style={{ color: "#f1f5f9", fontSize: 110, fontWeight: 800, lineHeight: 1, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.03em" }}>
            $50B
          </div>
          <div style={{ color: "#f59e0b", fontSize: 22, fontWeight: 600, marginTop: 12 }}>
            efficiency-led, revenue / GW
          </div>
          <div style={{ color: "#64748b", fontSize: 16, marginTop: 28, lineHeight: 1.5, fontFamily: "JetBrains Mono, monospace" }}>
            Strategy: efficiency.<br />
            $30B ARR · less compute, more output
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
