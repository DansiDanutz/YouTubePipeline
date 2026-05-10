import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// CTA — "Track the AI infrastructure race at zmarty.me"
// Editorial logo lockup with single accent
// 6s @ 30fps = 180 frames
export const Scene06CTA: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoEnter = spring({ frame: frame - 8, fps, config: { damping: 20, stiffness: 120 } });
  const tagEnter = interpolate(frame, [30, 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const urlEnter = spring({ frame: frame - 60, fps, config: { damping: 16 } });

  return (
    <AbsoluteFill style={{ background: "#05070a", fontFamily: "Inter, system-ui, sans-serif" }}>
      {/* Soft radial behind logo */}
      <div
        style={{
          position: "absolute",
          top: 380,
          left: 760,
          width: 400,
          height: 400,
          background: "radial-gradient(circle, rgba(16,185,129,0.22) 0%, transparent 70%)",
          filter: "blur(40px)",
          opacity: logoEnter,
        }}
      />

      {/* Logo lockup */}
      <div
        style={{
          position: "absolute",
          top: 410,
          left: 0,
          right: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 24,
          opacity: logoEnter,
          transform: `translateY(${interpolate(logoEnter, [0, 1], [30, 0])}px)`,
        }}
      >
        <div
          style={{
            width: 100,
            height: 100,
            background: "linear-gradient(135deg, #10b981 0%, #34d399 100%)",
            borderRadius: 22,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#05070a",
            fontSize: 64,
            fontWeight: 900,
            fontFamily: "Inter, system-ui",
            boxShadow: "0 0 60px rgba(16, 185, 129, 0.45)",
          }}
        >
          z
        </div>
        <div style={{ color: "#f1f5f9", fontSize: 92, fontWeight: 800, letterSpacing: "-0.02em" }}>
          ZmartyChat
        </div>
      </div>

      {/* Tagline */}
      <div
        style={{
          position: "absolute",
          top: 560,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#94a3b8",
          fontSize: 32,
          fontWeight: 500,
          opacity: tagEnter,
        }}
      >
        Track the AI infrastructure race in real time
      </div>

      {/* URL pill */}
      <div
        style={{
          position: "absolute",
          top: 660,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          opacity: urlEnter,
          transform: `translateY(${interpolate(urlEnter, [0, 1], [20, 0])}px)`,
        }}
      >
        <div
          style={{
            padding: "20px 56px",
            border: "1.5px solid #10b981",
            borderRadius: 999,
            color: "#10b981",
            fontSize: 36,
            fontFamily: "JetBrains Mono, monospace",
            letterSpacing: "0.05em",
            background: "rgba(16, 185, 129, 0.06)",
          }}
        >
          zmarty.me
        </div>
      </div>
    </AbsoluteFill>
  );
};
