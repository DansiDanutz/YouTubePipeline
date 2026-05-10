import React from "react";
import { AbsoluteFill, Sequence, getInputProps } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { ImageBeatRouter, ImageBeat } from "./ImageScenes";
import { TOKENS } from "./StoryScenes";

// ────────────────────────────────────────────────────────────────────────────
// StoryV3 — image-driven, data-driven story composition.
// Each beat has BOTH narrative data AND a path to its AI-generated hero image.
// Scenes display the image as full-bleed background with editorial overlays.
// ────────────────────────────────────────────────────────────────────────────

export type StoryV3Manifest = {
  beats: { duration_s: number; data: ImageBeat }[];
};

const FPS = 30;

// Default fallback (no images = falls back to dark editorial)
const DEFAULT_MANIFEST: StoryV3Manifest = {
  beats: [
    { duration_s: 7.0, data: { kind: "hook", eyebrow: "PIPELINE READY", statValue: 9, statSuffix: " steps", label: "Senior Production Pipeline" } },
    { duration_s: 6.5, data: { kind: "setup", eyebrow: "PROTAGONIST", mark: "?", name: "Pending", subtitle: "awaiting prompt", tags: ["Perplexity", "ElevenLabs", "Remotion"] } },
    { duration_s: 8.0, data: { kind: "conflict", eyebrow: "PROBLEM", headline: "—", bars: [{ label: "A", value: 1, unit: "" }, { label: "B", value: 2, unit: "" }, { label: "C", value: 3, unit: "" }, { label: "D", value: 4, unit: "" }], total: { label: "TOTAL", value: "" } } },
    { duration_s: 8.5, data: { kind: "breakthrough", eyebrow: "TURN", headline: "—", rows: [{ rank: 1, name: "—", value: "—", hero: true }] } },
    { duration_s: 7.0, data: { kind: "resolution", eyebrow: "OUTCOME", headline: "—", bigStat: "—", bigStatLabel: "—", stats: [{ v: "—", l: "—" }, { v: "—", l: "—" }, { v: "—", l: "—" }] } },
    { duration_s: 3.0, data: { kind: "cta", eyebrow: "BUILD YOURS", headline: "—", url: "—" } },
  ],
};

export const StoryV3Sequence: React.FC = () => {
  const props = getInputProps() as Partial<StoryV3Manifest>;
  const beats = props.beats && Array.isArray(props.beats) ? props.beats : DEFAULT_MANIFEST.beats;
  const transitionDuration = 14; // ~0.47s @ 30fps

  // Alternate between fade and slide for visual rhythm
  const transitions = [fade(), slide(), fade(), slide(), fade()];

  return (
    <>
      <AbsoluteFill style={{ backgroundColor: TOKENS.bg }} />
      <TransitionSeries>
        {beats.map((b, i) => {
          const sceneFrames = Math.round((b.duration_s || 6) * FPS);
          return (
            <React.Fragment key={i}>
              <TransitionSeries.Sequence durationInFrames={sceneFrames}>
                <ImageBeatRouter beat={b.data} durationFrames={sceneFrames} />
              </TransitionSeries.Sequence>
              {i < beats.length - 1 && (
                <TransitionSeries.Transition
                  presentation={transitions[i % transitions.length]}
                  timing={linearTiming({ durationInFrames: transitionDuration })}
                />
              )}
            </React.Fragment>
          );
        })}
      </TransitionSeries>
    </>
  );
};

export function calcStoryV3Duration(manifest: StoryV3Manifest = DEFAULT_MANIFEST): number {
  return manifest.beats.reduce((s, b) => s + Math.round((b.duration_s || 6) * FPS), 0);
}
