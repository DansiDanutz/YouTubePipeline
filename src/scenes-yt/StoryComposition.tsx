import React from "react";
import { AbsoluteFill, Sequence, getInputProps } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { wipe } from "@remotion/transitions/wipe";
import { BeatRouter, Beat, TOKENS } from "./StoryScenes";

// Default manifest (the YT-automation MoneyPrinter story) — used if no inputProps.
// At render time, pass `--props=<json-file>` to override with any story.
const DEFAULT_MANIFEST: { beats: { duration_s: number; data: Beat }[] } = {
  beats: [
    { duration_s: 7.0, data: { kind: "hook", eyebrow: "2023 · LATE OCTOBER", subLabel: "One GitHub repository.", statValue: 30346, label: "★ stars · in days" } },
    { duration_s: 6.5, data: { kind: "setup", eyebrow: "BEFORE THE BOOM · LATE 2023", mark: "?", name: "FujiwaraChoki", subtitle: "anonymous developer", tags: ["Python", "MoviePy", "OpenAI"] } },
    { duration_s: 8.0, data: { kind: "conflict", eyebrow: "BURNOUT · 26 HOURS PER VIDEO", headline: "The grind broke creators.", bars: [{ label: "SCRIPTING", value: 8, unit: "h" }, { label: "EDITING", value: 12, unit: "h" }, { label: "THUMBNAILS", value: 4, unit: "h" }, { label: "UPLOAD + SEO", value: 2, unit: "h" }], total: { label: "TOTAL", value: "26h × every single video" } } },
    { duration_s: 8.5, data: { kind: "breakthrough", eyebrow: "THE OPEN-SOURCE CASCADE · 2023 → 2026", headline: "MoneyPrinter ships → the floodgates open.", rows: [{ rank: 1, name: "FujiwaraChoki/MoneyPrinterV2", value: "30,346 ★", lang: "Python", hero: true }, { rank: 2, name: "FujiwaraChoki/MoneyPrinter", value: "13,158 ★", lang: "Python" }, { rank: 3, name: "RayVentura/ShortGPT", value: "7,303 ★", lang: "Python" }, { rank: 4, name: "SamurAIGPT/AI-Youtube-Shorts-Generator", value: "3,378 ★", lang: "Python" }, { rank: 5, name: "gyoridavid/short-video-maker", value: "1,110 ★", lang: "TypeScript" }] } },
    { duration_s: 7.0, data: { kind: "resolution", eyebrow: "A NEW CATEGORY · FACELESS YOUTUBE", headline: "Millions in revenue. No face required.", bigStat: "$8.3B", bigStatLabel: "virtual-influencer market in 2026", stats: [{ v: "82%", l: "of internet traffic = video" }, { v: "10×", l: "production speed via AI" }, { v: "0", l: "face required" }] } },
    { duration_s: 3.0, data: { kind: "cta", eyebrow: "BUILD YOURS · OPEN SOURCE", headline: "The tools are free.", url: "github.com/FujiwaraChoki/MoneyPrinterV2" } },
  ],
};

const FPS = 30;

export const StorySequence: React.FC = () => {
  // Remotion passes inputProps as the entire props object (no wrapper key).
  // Accept either { beats: [...] } (direct) or { manifest: { beats: [...] } } (wrapped).
  const props = getInputProps() as Partial<typeof DEFAULT_MANIFEST> & { manifest?: typeof DEFAULT_MANIFEST };
  const manifest = props.manifest || (props.beats ? { beats: props.beats } : DEFAULT_MANIFEST);
  const beats = manifest.beats;

  // Cinematic transitions — alternate fade / slide / wipe for visual rhythm
  const transitionDuration = 12; // 0.4s @ 30fps
  const presentationFor = (index: number) => {
    switch (index % 5) {
      case 1:
      case 4:
        return slide();
      case 2:
        return wipe();
      default:
        return fade();
    }
  };

  return (
    <>
      <AbsoluteFill style={{ backgroundColor: TOKENS.bg }} />
      <TransitionSeries>
        {beats.map((b, i) => {
          const sceneFrames = Math.round(b.duration_s * FPS);
          return (
            <React.Fragment key={i}>
              <TransitionSeries.Sequence durationInFrames={sceneFrames}>
                <BeatRouter beat={b.data} />
              </TransitionSeries.Sequence>
              {i < beats.length - 1 && (
                <TransitionSeries.Transition
                  presentation={presentationFor(i) as any}
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

// Calculate total frames for the composition
export function calcStoryDuration(manifest: typeof DEFAULT_MANIFEST = DEFAULT_MANIFEST): number {
  const transitionFrames = 12;
  const beatFrames = manifest.beats.reduce((s, b) => s + Math.round(b.duration_s * FPS), 0);
  // Transitions OVERLAP — they don't add to total length. So total = sum of scene durations.
  return beatFrames;
}
