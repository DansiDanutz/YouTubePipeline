import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
} from "remotion";
import { MeshGradient } from "../components/MeshGradient";
import { GlassCard } from "../components/GlassCard";
import { DataCounter } from "../components/DataCounter";

const tickers = [
  { sym: "IBIT", price: "$42.15", change: "+3.2%" },
  { sym: "FBTC", price: "$58.70", change: "+2.8%" },
  { sym: "ARKB", price: "$71.22", change: "+4.1%" },
  { sym: "BITB", price: "$19.85", change: "+2.5%" },
  { sym: "GBTC", price: "$48.90", change: "+1.9%" },
];

export const Scene04ETF: React.FC = () => {
  const frame = useCurrentFrame();

  const tickerOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const arrowReveal = interpolate(frame, [20, 45], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <MeshGradient variant="green" />

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
              fontFamily: '"Inter", sans-serif',
              fontSize: 18,
              letterSpacing: "0.3em",
              textTransform: "uppercase",
              color: "rgba(0,255,150,0.6)",
              textAlign: "center",
              marginBottom: 32,
            }}
          >
            Spot Bitcoin ETF Flows
          </div>

          {/* LED Ticker Board */}
          <div
            style={{
              display: "flex",
              gap: 16,
              justifyContent: "center",
              marginBottom: 48,
              opacity: tickerOpacity,
            }}
          >
            {tickers.map((t, i) => {
              const delay = i * 4;
              const itemOp = interpolate(frame, [delay, delay + 10], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              });
              return (
                <div
                  key={t.sym}
                  style={{
                    opacity: itemOp,
                    background: "rgba(0, 20, 10, 0.6)",
                    border: "1px solid rgba(0, 255, 150, 0.15)",
                    borderRadius: 12,
                    padding: "20px 24px",
                    minWidth: 140,
                    textAlign: "center",
                    boxShadow: "0 4px 20px rgba(0,0,0,0.3)",
                  }}
                >
                  <div
                    style={{
                      fontFamily: '"JetBrains Mono", monospace',
                      fontSize: 22,
                      fontWeight: 700,
                      color: "#fff",
                      marginBottom: 8,
                    }}
                  >
                    {t.sym}
                  </div>
                  <div
                    style={{
                      fontFamily: '"JetBrains Mono", monospace',
                      fontSize: 16,
                      color: "rgba(255,255,255,0.7)",
                    }}
                  >
                    {t.price}
                  </div>
                  <div
                    style={{
                      fontFamily: '"JetBrains Mono", monospace',
                      fontSize: 14,
                      color: "#00FF96",
                      marginTop: 4,
                    }}
                  >
                    {t.change}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Big Green Arrow & Number */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 24,
              opacity: arrowReveal,
              transform: `translateY(${interpolate(arrowReveal, [0, 1], [20, 0])}px)`,
            }}
          >
            <div
              style={{
                width: 0,
                height: 0,
                borderLeft: "24px solid transparent",
                borderRight: "24px solid transparent",
                borderBottom: "36px solid #00FF96",
                filter: "drop-shadow(0 0 12px rgba(0,255,150,0.5))",
              }}
            />
            <div
              style={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 72,
                fontWeight: 800,
                color: "#00FF96",
                textShadow: "0 0 40px rgba(0,255,150,0.35)",
              }}
            >
              <DataCounter
                value={3.5}
                prefix="+$"
                suffix="B"
                decimals={1}
                startFrame={40}
                duration={50}
              />
            </div>
          </div>

          <div
            style={{
              textAlign: "center",
              marginTop: 20,
              fontFamily: '"Inter", sans-serif',
              fontSize: 20,
              color: "rgba(255,255,255,0.5)",
              opacity: arrowReveal,
            }}
          >
            Total inflows turned positive this week
          </div>
        </GlassCard>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
