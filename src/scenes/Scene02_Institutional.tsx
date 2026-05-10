import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
} from "remotion";
import { MeshGradient } from "../components/MeshGradient";
import { GlassCard } from "../components/GlassCard";
import { DataCounter } from "../components/DataCounter";

export const Scene02Institutional: React.FC = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleY = interpolate(frame, [0, 20], [40, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const lineWidth = interpolate(frame, [20, 50], [0, 100], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const cardSpring = spring({
    frame: frame - 10,
    fps: 30,
    config: { damping: 14, mass: 1.2, stiffness: 70 },
  });

  return (
    <AbsoluteFill>
      <MeshGradient variant="dark" />

      <AbsoluteFill
        style={{
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          padding: 80,
        }}
      >
        <GlassCard>
          <div
            style={{
              opacity: titleOpacity,
              transform: `translateY(${titleY}px)`,
              textAlign: "center",
            }}
          >
            <div
              style={{
                fontFamily:
                  '"Inter", "SF Pro Display", -apple-system, sans-serif',
                fontSize: 20,
                fontWeight: 500,
                letterSpacing: "0.3em",
                textTransform: "uppercase",
                color: "rgba(255,215,0,0.7)",
                marginBottom: 20,
              }}
            >
              Institutional Confidence
            </div>

            <div
              style={{
                width: "120px",
                height: 2,
                background: "linear-gradient(90deg, transparent, #FFD700, transparent)",
                margin: "0 auto 32px",
                transform: `scaleX(${lineWidth / 100})`,
                transformOrigin: "center",
              }}
            />

            <div
              style={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 84,
                fontWeight: 800,
                color: "#fff",
                letterSpacing: "-0.02em",
                lineHeight: 1.1,
                textShadow: "0 0 40px rgba(255,215,0,0.15)",
              }}
            >
              <DataCounter
                value={145837}
                startFrame={30}
                duration={60}
                style={{ color: "#FFD700" }}
              />
              <span
                style={{
                  fontSize: 40,
                  color: "rgba(255,255,255,0.6)",
                  marginLeft: 16,
                  fontWeight: 500,
                }}
              >
                BTC
              </span>
            </div>

            <div
              style={{
                fontFamily: '"Inter", sans-serif',
                fontSize: 24,
                color: "rgba(255,255,255,0.55)",
                marginTop: 16,
                fontWeight: 400,
              }}
            >
              Strategy boosts holdings — a vote of confidence in Bitcoin
            </div>
          </div>

          {/* Decorative corner accents */}
          <div
            style={{
              position: "absolute",
              top: 20,
              left: 20,
              width: 40,
              height: 40,
              borderTop: "2px solid rgba(255,215,0,0.3)",
              borderLeft: "2px solid rgba(255,215,0,0.3)",
            }}
          />
          <div
            style={{
              position: "absolute",
              bottom: 20,
              right: 20,
              width: 40,
              height: 40,
              borderBottom: "2px solid rgba(255,215,0,0.3)",
              borderRight: "2px solid rgba(255,215,0,0.3)",
            }}
          />
        </GlassCard>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
