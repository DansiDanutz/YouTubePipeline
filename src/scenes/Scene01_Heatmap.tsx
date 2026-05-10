import React, { useEffect, useRef } from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
} from "remotion";
import { MeshGradient } from "../components/MeshGradient";

export const Scene01Heatmap: React.FC = () => {
  const frame = useCurrentFrame();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Heatmap data generation
  const rows = 24;
  const cols = 48;
  const cellW = 1920 / cols;
  const cellH = 1080 / rows;

  // $70,000 maps roughly to column 33 (center-right)
  const targetCol = 33;
  const targetRow = 12;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = 1920;
    canvas.height = 1080;

    // Clear
    ctx.clearRect(0, 0, 1920, 1080);

    // Draw heatmap grid
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const dist = Math.sqrt(
          Math.pow(c - targetCol, 2) + Math.pow(r - targetRow, 2) * 2
        );
        // Heat intensity: 1 at center, fading out
        let intensity = Math.max(0, 1 - dist / 10);
        // Add some noise
        intensity += (Math.random() - 0.5) * 0.15;
        intensity = Math.max(0, Math.min(1, intensity));

        // Pulse animation tied to frame
        const pulse = 1 + 0.15 * Math.sin(frame * 0.12);
        intensity *= pulse;

        // Color: cold (blue) to hot (red)
        const red = Math.floor(interpolate(intensity, [0, 0.5, 1], [10, 255, 255]));
        const green = Math.floor(interpolate(intensity, [0, 0.5, 1], [20, 100, 50]));
        const blue = Math.floor(interpolate(intensity, [0, 0.5, 1], [40, 50, 20]));
        const alpha = interpolate(intensity, [0, 1], [0.15, 0.95]);

        ctx.fillStyle = `rgba(${red}, ${green}, ${blue}, ${alpha})`;
        ctx.fillRect(
          c * cellW + 1,
          r * cellH + 1,
          cellW - 2,
          cellH - 2
        );
      }
    }

    // Draw glowing crosshair at $70K
    const cx = targetCol * cellW + cellW / 2;
    const cy = targetRow * cellH + cellH / 2;
    const glowSize = 120 + 20 * Math.sin(frame * 0.15);

    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowSize);
    glow.addColorStop(0, "rgba(255, 51, 102, 0.45)");
    glow.addColorStop(0.5, "rgba(255, 51, 102, 0.15)");
    glow.addColorStop(1, "rgba(255, 51, 102, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, 1920, 1080);

    // Grid lines (subtle)
    ctx.strokeStyle = "rgba(255,255,255,0.03)";
    ctx.lineWidth = 1;
    for (let c = 0; c <= cols; c++) {
      ctx.beginPath();
      ctx.moveTo(c * cellW, 0);
      ctx.lineTo(c * cellW, 1080);
      ctx.stroke();
    }
    for (let r = 0; r <= rows; r++) {
      ctx.beginPath();
      ctx.moveTo(0, r * cellH);
      ctx.lineTo(1920, r * cellH);
      ctx.stroke();
    }
  }, [frame]);

  // Typography animations
  const titleOpacity = interpolate(frame, [10, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleY = interpolate(frame, [10, 30], [30, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const priceSpring = spring({
    frame: frame - 40,
    fps: 30,
    config: { damping: 12, mass: 1, stiffness: 80 },
  });

  const subtitleOpacity = interpolate(frame, [70, 90], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <MeshGradient variant="red" />
      <canvas
        ref={canvasRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          opacity: 0.85,
        }}
      />

      {/* HUD Overlay */}
      <AbsoluteFill
        style={{
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          fontFamily:
            '"JetBrains Mono", "IBM Plex Mono", "SF Mono", monospace',
          color: "#fff",
          textShadow: "0 2px 24px rgba(0,0,0,0.6)",
        }}
      >
        <div
          style={{
            opacity: titleOpacity,
            transform: `translateY(${titleY}px)`,
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 18,
              letterSpacing: "0.35em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,0.55)",
              marginBottom: 16,
            }}
          >
            ZmartyChat Liquidation Heatmap
          </div>
        </div>

        <div
          style={{
            transform: `scale(${priceSpring})`,
            opacity: priceSpring,
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 96,
              fontWeight: 800,
              letterSpacing: "-0.02em",
              color: "#FF3366",
              textShadow: "0 0 60px rgba(255,51,102,0.5), 0 2px 12px rgba(0,0,0,0.8)",
              lineHeight: 1.1,
            }}
          >
            $70,000
          </div>
          <div
            style={{
              fontSize: 28,
              fontWeight: 500,
              color: "rgba(255,255,255,0.7)",
              marginTop: 8,
            }}
          >
            Critical Liquidation Zone
          </div>
        </div>

        <div
          style={{
            opacity: subtitleOpacity,
            marginTop: 32,
            display: "flex",
            alignItems: "baseline",
            gap: 12,
          }}
        >
          <span style={{ fontSize: 20, color: "rgba(255,255,255,0.5)" }}>
            Leveraged Longs Liquidated:
          </span>
          <span
            style={{
              fontSize: 40,
              fontWeight: 700,
              color: "#FFD700",
              textShadow: "0 0 30px rgba(255,215,0,0.3)",
            }}
          >
            $340.8M
          </span>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
