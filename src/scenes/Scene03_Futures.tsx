import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
} from "remotion";
import { MeshGradient } from "../components/MeshGradient";
import { GlassCard } from "../components/GlassCard";

export const Scene03Futures: React.FC = () => {
  const frame = useCurrentFrame();

  // Bar shrink animation
  const bar1Height = interpolate(frame, [10, 50], [420, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const bar2Height = interpolate(frame, [10, 50], [340, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Actually we want bar1 ($61B) to show, then shrink toward bar2 ($49B)
  // Let's show both bars with a transition
  const showBars = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const liquidatedText = spring({
    frame: frame - 60,
    fps: 30,
    config: { damping: 10, mass: 1, stiffness: 100 },
  });

  const arrowY = interpolate(frame, [30, 60], [-20, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <MeshGradient variant="red" />

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
              display: "flex",
              alignItems: "flex-end",
              justifyContent: "center",
              gap: 60,
              height: 480,
              opacity: showBars,
            }}
          >
            {/* $61B Bar */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div
                style={{
                  fontFamily: '"JetBrains Mono", monospace',
                  fontSize: 20,
                  color: "rgba(255,255,255,0.5)",
                  marginBottom: 12,
                }}
              >
                $61B
              </div>
              <div
                style={{
                  width: 100,
                  height: interpolate(frame, [10, 40], [0, 420], {
                    extrapolateLeft: "clamp",
                    extrapolateRight: "clamp",
                  }),
                  background:
                    "linear-gradient(180deg, rgba(255,80,80,0.9) 0%, rgba(180,30,30,0.6) 100%)",
                  borderRadius: "8px 8px 0 0",
                  boxShadow: "0 0 40px rgba(255,80,80,0.3)",
                }}
              />
              <div
                style={{
                  fontFamily: '"Inter", sans-serif',
                  fontSize: 16,
                  color: "rgba(255,255,255,0.4)",
                  marginTop: 12,
                  letterSpacing: "0.1em",
                }}
              >
                BEFORE
              </div>
            </div>

            {/* Arrow down */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                marginBottom: 60,
                transform: `translateY(${arrowY}px)`,
                opacity: interpolate(frame, [20, 40], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                }),
              }}
            >
              <div
                style={{
                  width: 0,
                  height: 0,
                  borderLeft: "16px solid transparent",
                  borderRight: "16px solid transparent",
                  borderTop: "24px solid #FF3366",
                  filter: "drop-shadow(0 0 8px rgba(255,51,102,0.6))",
                }}
              />
              <div
                style={{
                  width: 3,
                  height: 40,
                  background: "#FF3366",
                  marginTop: 4,
                }}
              />
            </div>

            {/* $49B Bar */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div
                style={{
                  fontFamily: '"JetBrains Mono", monospace',
                  fontSize: 20,
                  color: "rgba(255,255,255,0.5)",
                  marginBottom: 12,
                }}
              >
                $49B
              </div>
              <div
                style={{
                  width: 100,
                  height: interpolate(frame, [25, 55], [0, 340], {
                    extrapolateLeft: "clamp",
                    extrapolateRight: "clamp",
                  }),
                  background:
                    "linear-gradient(180deg, rgba(255,120,120,0.7) 0%, rgba(140,40,40,0.5) 100%)",
                  borderRadius: "8px 8px 0 0",
                }}
              />
              <div
                style={{
                  fontFamily: '"Inter", sans-serif',
                  fontSize: 16,
                  color: "rgba(255,255,255,0.4)",
                  marginTop: 12,
                  letterSpacing: "0.1em",
                }}
              >
                AFTER
              </div>
            </div>
          </div>

          {/* Liquidated text */}
          <div
            style={{
              textAlign: "center",
              marginTop: 40,
              transform: `scale(${liquidatedText})`,
              opacity: liquidatedText,
            }}
          >
            <div
              style={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 56,
                fontWeight: 800,
                color: "#FF3366",
                textShadow: "0 0 30px rgba(255,51,102,0.4)",
                letterSpacing: "0.05em",
              }}
            >
              $2.5B LIQUIDATED
            </div>
            <div
              style={{
                fontFamily: '"Inter", sans-serif',
                fontSize: 20,
                color: "rgba(255,255,255,0.5)",
                marginTop: 8,
              }}
            >
              Futures open interest collapsed in 72 hours
            </div>
          </div>
        </GlassCard>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
