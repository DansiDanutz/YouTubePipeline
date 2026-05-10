import React, { useEffect, useRef } from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";

export const MeshGradient: React.FC<{ variant?: "dark" | "red" | "green" }> = ({
  variant = "dark",
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frame = useCurrentFrame();

  const palette = {
    dark: [
      [10, 14, 26],
      [15, 22, 40],
      [8, 12, 24],
      [20, 30, 55],
    ],
    red: [
      [20, 8, 12],
      [40, 10, 18],
      [10, 6, 10],
      [55, 14, 22],
    ],
    green: [
      [8, 18, 12],
      [12, 35, 20],
      [6, 14, 10],
      [18, 50, 28],
    ],
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = 1920;
    const h = 1080;
    canvas.width = w;
    canvas.height = h;

    const colors = palette[variant];
    const t = frame * 0.008;

    // Draw large soft blobs
    for (let i = 0; i < 4; i++) {
      const cx = w * (0.2 + 0.6 * Math.sin(t + i * 1.7));
      const cy = h * (0.2 + 0.6 * Math.cos(t * 0.7 + i * 2.1));
      const r = 600 + 200 * Math.sin(t + i);

      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      const [r1, g1, b1] = colors[i];
      grad.addColorStop(0, `rgba(${r1}, ${g1}, ${b1}, 0.55)`);
      grad.addColorStop(1, `rgba(${r1}, ${g1}, ${b1}, 0)`);

      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }

    // Overlay noise grain
    const imageData = ctx.getImageData(0, 0, w, h);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
      const noise = (Math.random() - 0.5) * 12;
      data[i] = Math.min(255, Math.max(0, data[i] + noise));
      data[i + 1] = Math.min(255, Math.max(0, data[i + 1] + noise));
      data[i + 2] = Math.min(255, Math.max(0, data[i + 2] + noise));
    }
    ctx.putImageData(imageData, 0, 0);
  }, [frame, variant]);

  return (
    <AbsoluteFill>
      <canvas
        ref={canvasRef}
        style={{
          width: "100%",
          height: "100%",
          position: "absolute",
          top: 0,
          left: 0,
        }}
      />
    </AbsoluteFill>
  );
};
