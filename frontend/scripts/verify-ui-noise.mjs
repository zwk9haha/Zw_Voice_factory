import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const readSource = (relativePath) => readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8");

const preparationWorkspace = readSource("../src/PreparationWorkspace.tsx");
const projectPreparationWorkspace = readSource("../src/ProjectPreparationWorkspace.tsx");
const qualityWorkbench = readSource("../src/QualityWorkbench.tsx");
const referenceAudioPanel = readSource("../src/ReferenceAudioPanel.tsx");
const waveform = readSource("../src/Waveform.tsx");
const rvcWorkbench = readSource("../src/RvcWorkbench.tsx");
const app = readSource("../src/App.tsx");
const styles = readSource("../src/styles.css");
const launcher = readSource("../../scripts/start_factory.ps1");

for (const [name, source] of [
  ["PreparationWorkspace", preparationWorkspace],
  ["ProjectPreparationWorkspace", projectPreparationWorkspace],
]) {
  if (/<audio\b[^>]*\bcontrols\b/i.test(source)) {
    throw new Error(`${name} still exposes the browser-native audio control surface`);
  }
}

if (!projectPreparationWorkspace.includes("<AudioPlayer")) {
  throw new Error("Visible previews must use the shared AudioPlayer");
}

if (!styles.includes(".audio-player") || !styles.includes("background: transparent;")) {
  throw new Error("The shared audio player must inherit its surrounding surface");
}

if (/const\s+waveform\s*=\s*\[/i.test(waveform) || !waveform.includes("decodeAudioData") || !waveform.includes("fetch(src")) {
  throw new Error("Waveform must decode each local audio source instead of rendering a fixed bar pattern");
}

if (!referenceAudioPanel.includes("<Waveform") || !referenceAudioPanel.includes("src={reference.audio_url}")) {
  throw new Error("Reference audio panels must render the decoded waveform for the active audio version");
}

if (!qualityWorkbench.includes("<Waveform src={activeReferenceUrl}") || !qualityWorkbench.includes("<Waveform src={resultUrl}")) {
  throw new Error("Quality rendering must bind waveforms to each actual reference and result URL");
}

const checkPaneIndex = projectPreparationWorkspace.indexOf("prep-check-pane");
const referenceDockIndex = projectPreparationWorkspace.indexOf('<section className="reference-review-dock">');
if (checkPaneIndex < 0 || referenceDockIndex < checkPaneIndex) {
  throw new Error("Reference audio must be docked above the stage gate inside the check pane");
}

if (!projectPreparationWorkspace.includes("open={referenceAudioExpanded}") || projectPreparationWorkspace.includes('open={activeStage === "references"')) {
  throw new Error("Character review and canonical reference must share one reference-audio disclosure state");
}

for (const operation of ["生成脚本文件", "导出 JSON", "删除脚本缓存"]) {
  if (!projectPreparationWorkspace.includes(operation)) {
    throw new Error(`Director stage is missing the ${operation} operation`);
  }
}

if (!styles.includes("grid-template-rows: auto auto auto auto minmax(0, 1fr) auto") || !styles.includes("grid-template-rows: auto auto minmax(0, 1fr) auto")) {
  throw new Error("Quality workbench panes must reserve explicit rows for the new toolbars");
}

if (!app.includes("enabled: showResources") || !app.includes("refetchInterval: showResources ?")) {
  throw new Error("System resource polling must pause while the monitor is hidden");
}

if (!launcher.includes("'--no-access-log'")) {
  throw new Error("The backend launcher must suppress routine HTTP access logs");
}

for (const marker of ["fetchAnalysisActivity", "analysis-progress-panel", "AnalysisEventBox", "处理进度"]) {
  if (!projectPreparationWorkspace.includes(marker)) {
    throw new Error(`Novel import analysis monitor is missing ${marker}`);
  }
}

for (const marker of ["cancelPreparationAction", "preparation/cancel", "preparation-cancel-button", "终止"]) {
  if (!`${projectPreparationWorkspace}\n${readSource("../src/api.ts")}`.includes(marker)) {
    throw new Error(`Novel import cancellation is missing ${marker}`);
  }
}

for (const marker of ["startContinuousProduction", "一键开始质量生产", "continuous-run-summary", "emotion_policy"]) {
  if (!projectPreparationWorkspace.includes(marker)) {
    throw new Error(`Continuous production source controls are missing ${marker}`);
  }
}

for (const marker of ["fetchContinuousProduction", "quality-slice-queue", "locateProductionSlice"]) {
  if (!qualityWorkbench.includes(marker)) {
    throw new Error(`Quality slice queue is missing ${marker}`);
  }
}

if (!styles.includes(".continuous-production-control") || !styles.includes(".quality-slice-queue")) {
  throw new Error("Continuous production controls and slice queue styles are missing");
}

if (!styles.includes(".analysis-monitor") || !styles.includes(".analysis-event-box")) {
  throw new Error("Novel import analysis monitor styles are missing");
}

if (!app.includes("getBoundingClientRect()") || !app.includes("<RvcWorkbench") || !styles.includes("clip-path: circle(0 at var(--rvc-origin-x) var(--rvc-origin-y))")) {
  throw new Error("RVC workbench must expand from the route button into a full-screen surface");
}

if (!rvcWorkbench.includes('aria-label="RVC 速度路线工作台"') || !rvcWorkbench.includes("createRvcTrainingJob") || !rvcWorkbench.includes("updateRvcSettings")) {
  throw new Error("RVC workbench must expose managed training and persisted stability bindings");
}

if (app.includes("setRvcEnabled") || !styles.includes("prefers-reduced-motion: reduce")) {
  throw new Error("RVC stability must use backend state and provide reduced-motion behavior");
}

console.log("UI audio theming and runtime log noise verified");
