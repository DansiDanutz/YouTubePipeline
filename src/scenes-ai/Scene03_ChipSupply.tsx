import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// Evidence 1 — "Chip supply: NVIDIA H200/Blackwell, TSMC bottleneck"
// Bar chart: GPU demand vs supply
// 7s @ 30fps = 210 frames
export const Scene03ChipSupply: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 20 } });
  const demandBar = spring({ frame: frame - 30, fps, config: { damping: 14, stiffness: 60 } });
  const supplyBar = spring({ frame: frame - 60, fps, config: { damping: 14, stiffness: 60 } });
  const gapLabel = interpolate(frame, [110, 140], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: "#05070a", fontFamily: "Inter, system-ui, sans-serif" }}>
      <div
        style={{
          position: "absolute",
          top: 100,
          left: 80,
          color: "#64748b",
          fontSize: 18,
          letterSpacing: "0.4em",
          fontFamily: "JetBrains Mono, monospace",
          opacity: titleEnter,
        }}
      >
        EVIDENCE · CHIP SUPPLY
      </div>

      <div
        style={{
          position: "absolute",
          top: 145,
          left: 80,
          color: "#f1f5f9",
          fontSize: 56,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          lineHeight: 1.1,
          opacity: titleEnter,
        }}
      >
        Demand has out-run TSMC.
      </div>

      {/* Bar chart container */}
      <div style={{ position: "absolute", top: 320, left: 80, right: 80, bottom: 140 }}>
        {/* Y-axis label */}
        <div style={{ position: "absolute", left: 0, top: 0, color: "#475569", fontSize: 14, fontFamily: "JetBrains Mono, monospace", letterSpacing: "0.2em" }}>
          H200 / BLACKWELL UNITS · 2026 EST.
        </div>

        {/* Demand bar */}
        <div style={{ position: "absolute", left: 60, top: 60, right: 60 }}>
          <div style={{ display: "flex", alignItems: "baseline", marginBottom: 12 }}>
            <span style={{ color: "#94a3b8", fontFamily: "JetBrains Mono, monospace", fontSize: 16, width: 200 }}>DEMAND</span>
            <span style={{ color: "#10b981", fontSize: 38, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>5.2M units</span>
          </div>
          <div
            style={{
              height: 64,
              background: "linear-gradient(90deg, #10b981 0%, #34d399 100%)",
              width: `${demandBar * 95}%`,
              borderRadius: 2,
              boxShadow: "0 0 28px rgba(16, 185, 129, 0.45)",
            }}
          />
        </div>

        {/* Supply bar */}
        <div style={{ position: "absolute", left: 60, top: 230, right: 60 }}>
          <div style={{ display: "flex", alignItems: "baseline", marginBottom: 12 }}>
            <span style={{ color: "#94a3b8", fontFamily: "JetBrains Mono, monospace", fontSize: 16, width: 200 }}>SUPPLY</span>
            <span style={{ color: "#f59e0b", fontSize: 38, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>2.1M units</span>
          </div>
          <div
            style={{
              height: 64,
              background: "linear-gradient(90deg, #f59e0b 0%, #fbbf24 100%)",
              width: `${supplyBar * 38}%`,
              borderRadius: 2,
              boxShadow: "0 0 28px rgba(245, 158, 11, 0.4)",
            }}
          />
        </div>

        {/* Gap callout */}
        <div
          style={{
            position: "absolute",
            top: 410,
            left: 60,
            opacity: gapLabel,
            color: "#ef4444",
            fontSize: 22,
            fontFamily: "JetBrains Mono, monospace",
            letterSpacing: "0.05em",
          }}
        >
          ▲ <span style={{ color: "#fca5a5" }}>3.1M unit shortfall — every quarter</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};
