import React, { useMemo } from "react";
import { interpolate, useCurrentFrame } from "remotion";

interface DataCounterProps {
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  startFrame?: number;
  duration?: number;
  style?: React.CSSProperties;
}

export const DataCounter: React.FC<DataCounterProps> = ({
  value,
  prefix = "",
  suffix = "",
  decimals = 0,
  startFrame = 0,
  duration = 30,
  style,
}) => {
  const frame = useCurrentFrame();

  const current = interpolate(
    frame,
    [startFrame, startFrame + duration],
    [0, value],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }
  );

  const formatted = useMemo(() => {
    const n = current.toFixed(decimals);
    // Add comma separators
    const parts = n.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
  }, [current, decimals]);

  return (
    <span
      style={{
        fontVariantNumeric: "tabular-nums",
        fontFeatureSettings: '"tnum"',
        ...style,
      }}
    >
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
};
