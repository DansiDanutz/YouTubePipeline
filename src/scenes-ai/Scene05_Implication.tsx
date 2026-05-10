import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

// Implication — "OpenAI and Anthropic race for chips and power"
// Two converging arcs meeting at "compute"
// 7s @ 30fps = 210 frames
export const Scene05Implication: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleEnter = spring({ frame: frame - 5, fps, config: { damping: 18 } });
  const arcDraw = interpolate(frame, [25, 130], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - Math.pow(1 - t, 3) });
  const focalEnter = spring({ frame: frame - 110, fps, config: { damping: 14 } });

  // Path lengths approx
  const len = 1100;

  return (
    <AbsoluteFill style={{ background: "#05070a", fontFamily: "Inter, system-ui, sans-serif" }}>
      <div
        style={{
          position: "absolute",
          top: 100,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#64748b",
          fontSize: 18,
          letterSpacing: "0.4em",
          fontFamily: "JetBrains Mono, monospace",
          opacity: titleEnter,
        }}
      >
        IMPLICATION
      </div>
      <div
        style={{
          position: "absolute",
          top: 155,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#f1f5f9",
          fontSize: 56,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          opacity: titleEnter,
        }}
      >
        Two strategies, one chokepoint.
      </div>

      {/* Convergence diagram */}
      <svg
        width="1920"
        height="1080"
        style={{ position: "absolute", inset: 0 }}
        viewBox="0 0 1920 1080"
      >
        {/* OpenAI arc — top-left to center */}
        <path
          d="M 200 480 Q 600 380 960 600"
          stroke="#10b981"
          strokeWidth="3"
          fill="none"
          strokeDasharray={len}
          strokeDashoffset={len * (1 - arcDraw)}
          opacity={0.85}
        />
        {/* Anthropic arc — top-right to center */}
        <path
          d="M 1720 480 Q 1320 380 960 600"
          stroke="#f59e0b"
          strokeWidth="3"
          fill="none"
          strokeDasharray={len}
          strokeDashoffset={len * (1 - arcDraw)}
          opacity={0.85}
        />

        {/* OpenAI label */}
        <g transform="translate(200, 480)" opacity={arcDraw}>
          <circle r="12" fill="#10b981" />
          <text x="-30" y="-25" fill="#10b981" fontSize="22" fontFamily="JetBrains Mono, monospace" letterSpacing="0.2em">
            OPENAI
          </text>
        </g>

        {/* Anthropic label */}
        <g transform="translate(1720, 480)" opacity={arcDraw}>
          <circle r="12" fill="#f59e0b" />
          <text x="-110" y="-25" fill="#f59e0b" fontSize="22" fontFamily="JetBrains Mono, monospace" letterSpacing="0.2em">
            ANTHROPIC
          </text>
        </g>

        {/* Focal node */}
        <g transform="translate(960, 600)" opacity={focalEnter}>
          <circle r={18 + focalEnter * 18} fill="#ef4444" opacity={0.18} />
          <circle r="14" fill="#ef4444" />
        </g>
      </svg>

      {/* Focal label */}
      <div
        style={{
          position: "absolute",
          top: 660,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#fca5a5",
          fontSize: 36,
          fontFamily: "JetBrains Mono, monospace",
          letterSpacing: "0.15em",
          opacity: focalEnter,
        }}
      >
        COMPUTE · CHIPS · POWER
      </div>
      <div
        style={{
          position: "absolute",
          top: 730,
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#94a3b8",
          fontSize: 22,
          opacity: focalEnter,
        }}
      >
        Whoever controls supply, controls the next 18 months.
      </div>
    </AbsoluteFill>
  );
};
