import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

interface GlassCardProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  delay?: number;
}

export const GlassCard: React.FC<GlassCardProps> = ({
  children,
  style,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [delay, delay + 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const translateY = interpolate(frame, [delay, delay + 20], [40, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        ...style,
      }}
    >
      <div
        style={{
          opacity,
          transform: `translateY(${translateY}px)`,
          background: "rgba(10, 14, 26, 0.72)",
          backdropFilter: "blur(24px) saturate(140%)",
          WebkitBackdropFilter: "blur(24px) saturate(140%)",
          border: "1px solid rgba(255, 255, 255, 0.08)",
          borderRadius: 24,
          padding: "48px 64px",
          boxShadow:
            "0 24px 64px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255,255,255,0.06)",
          maxWidth: 1400,
          width: "100%",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* subtle inner glow top */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: "20%",
            right: "20%",
            height: 1,
            background:
              "linear-gradient(90deg, transparent, rgba(0, 212, 255, 0.35), transparent)",
          }}
        />
        {children}
      </div>
    </AbsoluteFill>
  );
};
