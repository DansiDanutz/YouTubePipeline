import { AbsoluteFill, Composition, Sequence } from "remotion";
import { Scene01Heatmap } from "./scenes/Scene01_Heatmap";
import { Scene02Institutional } from "./scenes/Scene02_Institutional";
import { Scene03Futures } from "./scenes/Scene03_Futures";
import { Scene04ETF } from "./scenes/Scene04_ETF";
import { Scene05Stability } from "./scenes/Scene05_Stability";
import { Scene06CTA } from "./scenes/Scene06_CTA";
// AI Compute composition
import { Scene01Hook as AIScene01Hook } from "./scenes-ai/Scene01_Hook";
import { Scene02Thesis as AIScene02Thesis } from "./scenes-ai/Scene02_Thesis";
import { Scene03ChipSupply as AIScene03Chip } from "./scenes-ai/Scene03_ChipSupply";
import { Scene04DataCenters as AIScene04DC } from "./scenes-ai/Scene04_DataCenters";
import { Scene05Implication as AIScene05Imp } from "./scenes-ai/Scene05_Implication";
import { Scene06CTA as AIScene06CTA } from "./scenes-ai/Scene06_CTA";
// YouTube Story composition
import {
  Scene1Hook as YTScene1Hook,
  Scene2Setup as YTScene2Setup,
  Scene3Conflict as YTScene3Conflict,
  Scene4Breakthrough as YTScene4Breakthrough,
  Scene5Resolution as YTScene5Resolution,
  Scene6CTA as YTScene6CTA,
} from "./scenes-yt/YouTubeStory";
// Production data-driven story composition (StoryV2 — accepts manifest via inputProps)
import { StorySequence, calcStoryDuration } from "./scenes-yt/StoryComposition";
// Production v3 — image-driven (each scene displays an AI-generated hero image)
import { StoryV3Sequence, calcStoryV3Duration } from "./scenes-yt/StoryV3";

export const RemotionVideo: React.FC = () => {
  return (
    <>
      <Composition
        id="ZmartyBitcoin"
        component={MainSequence}
        durationInFrames={1240}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{}}
      />
      <Composition
        id="AICompute"
        component={AIComputeSequence}
        durationInFrames={1200}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{}}
      />
      <Composition
        id="YouTubeStory"
        component={YouTubeStorySequence}
        durationInFrames={1200}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{}}
      />
      <Composition
        id="StoryV2"
        component={StorySequence}
        durationInFrames={calcStoryDuration()}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{}}
        calculateMetadata={({ props }) => {
          // props can be either { beats: [...] } directly OR { manifest: { beats: [...] } }
          const p = props as any;
          const beats = p?.beats || p?.manifest?.beats;
          if (beats && Array.isArray(beats)) {
            const total = beats.reduce((s: number, b: any) => s + Math.round((b.duration_s || 0) * 30), 0);
            return { durationInFrames: total > 0 ? total : calcStoryDuration() };
          }
          return { durationInFrames: calcStoryDuration() };
        }}
      />
      <Composition
        id="StoryV3"
        component={StoryV3Sequence}
        durationInFrames={calcStoryV3Duration()}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{}}
        calculateMetadata={({ props }) => {
          const beats = (props as any)?.beats;
          if (beats && Array.isArray(beats)) {
            const total = beats.reduce((s: number, b: any) => s + Math.round((b.duration_s || 0) * 30), 0);
            return { durationInFrames: total > 0 ? total : calcStoryV3Duration() };
          }
          return { durationInFrames: calcStoryV3Duration() };
        }}
      />
    </>
  );
};

// YouTube Story · 1200 frames · 40s
//   Scene 1 Hook         0–7s    (0–209)    210 frames
//   Scene 2 Setup        7–13.5s (210–404)  195 frames
//   Scene 3 Conflict    13.5–21.5s(405–644) 240 frames
//   Scene 4 Breakthrough 21.5–30s (645–899) 255 frames
//   Scene 5 Resolution   30–37s   (900–1109) 210 frames
//   Scene 6 CTA          37–40s   (1110–1199) 90 frames
const YouTubeStorySequence: React.FC = () => {
  return (
    <>
      <AbsoluteFill style={{ backgroundColor: "#0a0a0c" }} />
      <Sequence from={0} durationInFrames={210}><YTScene1Hook /></Sequence>
      <Sequence from={210} durationInFrames={195}><YTScene2Setup /></Sequence>
      <Sequence from={405} durationInFrames={240}><YTScene3Conflict /></Sequence>
      <Sequence from={645} durationInFrames={255}><YTScene4Breakthrough /></Sequence>
      <Sequence from={900} durationInFrames={210}><YTScene5Resolution /></Sequence>
      <Sequence from={1110} durationInFrames={90}><YTScene6CTA /></Sequence>
    </>
  );
};

const MainSequence: React.FC = () => {
  return (
    <>
      <AbsoluteFill style={{ backgroundColor: "#05070a" }} />
      {/* SCENA 1: HOOK — Heatmap 0-8s (0-239) */}
      <Sequence from={0} durationInFrames={285}>
        <Scene01Heatmap />
      </Sequence>

      {/* SCENA 2: THESIS — Institutional 9.5-17.7s */}
      <Sequence from={285} durationInFrames={245}>
        <Scene02Institutional />
      </Sequence>

      {/* SCENA 3: EVIDENCE — Futures Decline 17.7-26.5s */}
      <Sequence from={530} durationInFrames={265}>
        <Scene03Futures />
      </Sequence>

      {/* SCENA 4: EVIDENCE — ETF Inflows 26.5-31.5s */}
      <Sequence from={795} durationInFrames={150}>
        <Scene04ETF />
      </Sequence>

      {/* SCENA 5: IMPLICATION — Stability 31.5-36.0s */}
      <Sequence from={945} durationInFrames={135}>
        <Scene05Stability />
      </Sequence>

      {/* SCENA 6: CTA — ZmartyChat 36.0-41.3s */}
      <Sequence from={1080} durationInFrames={160}>
        <Scene06CTA />
      </Sequence>
    </>
  );
};

// AI Compute composition — 1200 frames @ 30fps = 40s exact
// Scene 1 hook       0-7s    (0-209)   210 frames
// Scene 2 thesis     7-13.5s  (210-404) 195 frames
// Scene 3 chip       13.5-20.5s (405-614) 210 frames
// Scene 4 datacenter 20.5-27s   (615-809) 195 frames
// Scene 5 implication 27-34s   (810-1019) 210 frames
// Scene 6 CTA        34-40s    (1020-1199) 180 frames
const AIComputeSequence: React.FC = () => {
  return (
    <>
      <AbsoluteFill style={{ backgroundColor: "#05070a" }} />
      <Sequence from={0} durationInFrames={210}>
        <AIScene01Hook />
      </Sequence>
      <Sequence from={210} durationInFrames={195}>
        <AIScene02Thesis />
      </Sequence>
      <Sequence from={405} durationInFrames={210}>
        <AIScene03Chip />
      </Sequence>
      <Sequence from={615} durationInFrames={195}>
        <AIScene04DC />
      </Sequence>
      <Sequence from={810} durationInFrames={210}>
        <AIScene05Imp />
      </Sequence>
      <Sequence from={1020} durationInFrames={180}>
        <AIScene06CTA />
      </Sequence>
    </>
  );
};

export default RemotionVideo;
