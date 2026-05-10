import React, { useEffect, useRef } from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
} from "remotion";
import { MeshGradient } from "../components/MeshGradient";

export const Scene05Stability: React.FC = () => {
  const frame = useCurrentFrame();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = 1920;
    canvas.height = 1080;
    const w = 1920;
    const h = 1080;
    const pad = 160;
    const graphW = w - pad * 2;
    const graphH = 500;
    const graphY = (h - graphH) / 2;

    // Progress of morph (0 = chaotic, 1 = stable)
    const morph = interpolate(frame, [0, 120], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

    // Clear
    ctx.clearRect(0, 0, w, h);

    // Draw grid
    ctx.strokeStyle = `rgba(255,255,255,${0.05 + morph * 0.05})`;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 10; i++) {
      const x = pad + (graphW / 10) * i;
      ctx.beginPath();
      ctx.moveTo(x, graphY);
      ctx.lineTo(x, graphY + graphH);
      ctx.stroke();
    }
    for (let i = 0; i <= 6; i++) {
      const y = graphY + (graphH / 6) * i;
      ctx.beginPath();
      ctx.moveTo(pad, y);
      ctx.lineTo(pad + graphW, y);
      ctx.stroke();
    }

    // Generate points
    const points = 80;
    const xStep = graphW / (points - 1);

    // Chaotic path (high volatility)
    const chaoticY = (i: number) => {
      const base = Math.sin(i * 0.5) * 120;
      const noise = Math.sin(i * 2.3 + 1.7) * 80 + Math.cos(i * 4.1) * 40;
      const spike = i === 35 || i === 36 ? -180 : 0;
      return graphY + graphH / 2 + base + noise + spike;
    };

    // Stable path (smooth sine)
    const stableY = (i: number) => {
      return graphY + graphH / 2 + Math.sin(i * 0.15) * 40;
    };

    // Current morphed path
    const currentY = (i: number) => {
      return chaoticY(i) * (1 - morph) + stableY(i) * morph;
    };

    // Draw filled area under curve
    const gradient = ctx.createLinearGradient(0, graphY, 0, graphY + graphH);
    const r1 = Math.floor(interpolate(morph, [0, 1], [255, 0]));
    const g1 = Math.floor(interpolate(morph, [0, 1], [50, 180]));
    const b1 = Math.floor(interpolate(morph, [0, 1], [80, 255]));
    gradient.addColorStop(0, `rgba(${r1}, ${g1}, ${b1}, 0.35)`);
    gradient.addColorStop(1, `rgba(${r1}, ${g1}, ${b1}, 0)`);

    ctx.beginPath();
    ctx.moveTo(pad, graphY + graphH);
    for (let i = 0; i < points; i++) {
      const x = pad + i * xStep;
      const y = currentY(i);
      ctx.lineTo(x, y);
    }
    ctx.lineTo(pad + graphW, graphY + graphH);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Draw line
    ctx.beginPath();
    ctx.lineWidth = 4;
    ctx.strokeStyle = `rgb(${r1}, ${g1}, ${b1})`;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (let i = 0; i < points; i++) {
      const x = pad + i * xStep;
      const y = currentY(i);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Glow under line
    ctx.beginPath();
    ctx.lineWidth = 12;
    ctx.strokeStyle = `rgba(${r1}, ${g1}, ${b1}, 0.25)`;
    for (let i = 0; i < points; i++) {
      const x = pad + i * xStep;
      const y = currentY(i);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Draw dot at end
    const endX = pad + (points - 1) * xStep;
    const endY = currentY(points - 1);
    ctx.beginPath();
    ctx.arc(endX, endY, 8, 0, Math.PI * 2);
    ctx.fillStyle = `rgb(${r1}, ${g1}, ${b1})`;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(endX, endY, 20, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${r1}, ${g1}, ${b1}, 0.2)`;
    ctx.fill();
  }, [frame]);

  const textOpacity = interpolate(frame, [100, 130], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <MeshGradient variant={frame < 120 ? "red" : "dark"} />
      <canvas
        ref={canvasRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
        }}
      />

      <AbsoluteFill
        style={{
          flexDirection: "column",
          justifyContent: "flex-end",
          alignItems: "center",
          paddingBottom: 120,
          pointerEvents: "none",
        }}
      >
        <div
          style={{
            opacity: textOpacity,
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontFamily: '"Inter", sans-serif',
              fontSize: 36,
              fontWeight: 600,
              color: "#fff",
              textShadow: "0 2px 24px rgba(0,0,0,0.6)",
            }}
          >
            Bitcoin may stabilize
          </div>
          <div
            style={{
              fontFamily: '"Inter", sans-serif',
              fontSize: 22,
              color: "rgba(255,255,255,0.6)",
              marginTop: 8,
            }}
          >
            Reducing retail-driven volatility as institutions absorb supply
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
