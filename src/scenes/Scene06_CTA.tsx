import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
} from "remotion";
import { MeshGradient } from "../components/MeshGradient";

export const Scene06CTA: React.FC = () => {
  const frame = useCurrentFrame();

  const logoSpring = spring({
    frame,
    fps: 30,
    config: { damping: 12, mass: 1, stiffness: 100 },
  });

  const textOp = interpolate(frame, [15, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const urlOp = interpolate(frame, [30, 45], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <MeshGradient variant="dark" />

      {/* Subtle vignette */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at center, transparent 30%, rgba(0,0,0,0.6) 100%)",
        }}
      />

      <AbsoluteFill
        style={{
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          gap: 24,
        }}
      >
        {/* Logo mark */}
        <div
          style={{
            transform: `scale(${logoSpring})`,
            opacity: logoSpring,
            display: "flex",
            alignItems: "center",
            gap: 20,
          }}
        >
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: 18,
              background: "linear-gradient(135deg, #00D4FF, #0088FF)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 0 40px rgba(0,212,255,0.35)",
            }}
          >
            <span
              style={{
                color: "#fff",
                fontSize: 32,
                fontWeight: 800,
                fontFamily: '"Inter", sans-serif',
              }}
            >
              Z
            </span>
          </div>
          <span
            style={{
              fontFamily: '"Inter", sans-serif',
              fontSize: 52,
              fontWeight: 800,
              color: "#fff",
              letterSpacing: "-0.02em",
            }}
          >
            ZmartyChat
          </span>
        </div>

        {/* Tagline */}
        <div
          style={{
            opacity: textOp,
            fontFamily: '"Inter", sans-serif',
            fontSize: 24,
            color: "rgba(255,255,255,0.65)",
            fontWeight: 400,
            textAlign: "center",
            maxWidth: 600,
            lineHeight: 1.4,
          }}
        >
          Real-time leverage analytics for serious traders
        </div>

        {/* URL */}
        <div
          style={{
            opacity: urlOp,
            marginTop: 8,
            padding: "14px 36px",
            borderRadius: 50,
            border: "1.5px solid rgba(0,212,255,0.4)",
            background: "rgba(0,212,255,0.08)",
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 22,
            color: "#00D4FF",
            letterSpacing: "0.05em",
            boxShadow: "0 0 30px rgba(0,212,255,0.1)",
          }}
        >
          zmarty.me
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
