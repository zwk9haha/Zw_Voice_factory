import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const sourceUrl = new URL("../src/qualityRouting.ts", import.meta.url);
const source = readFileSync(fileURLToPath(sourceUrl), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  fileName: "qualityRouting.ts",
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { collectReusableQualityJobs, resolveQualityVoice } = await import(moduleUrl);

const reference = (reference_id, source_character_id, gender, selection_mode, selected = true) => ({
  reference_id,
  source_character_id,
  display_name: reference_id,
  gender,
  selection_mode,
  selected,
});
const references = [
  reference("narrator-male", "narrator", "male", "narrator_default"),
  reference("narrator-female", "narrator", "female", "narrator_default"),
  reference("hero-a", "hero-a", "male", "automatic"),
  reference("hero-b", "hero-b", "female", "automatic"),
  reference("guard", "guard", "unknown", "optional", false),
];
const characters = [
  { character_id: "hero-a", display_name: "男主", aliases: [], gender: "male", tier: "core" },
  { character_id: "woman", display_name: "陌生女子", aliases: [], gender: "female", tier: "minor" },
  { character_id: "guard", display_name: "路人守卫", aliases: [], gender: "unknown", tier: "minor" },
];
const segment = (overrides = {}) => ({
  segment_id: "segment-1",
  chapter_id: "chapter-1",
  character_id: "narrator",
  voice_reference_id: null,
  speaker_gender: "unknown",
  speaker_kind: "unknown",
  text: "继续。",
  segment_type: "dialogue",
  direction: {},
  ...overrides,
});
const resolve = (segmentValue, narratorGender = "female") => resolveQualityVoice({
  segment: segmentValue,
  references,
  characters,
  narratorGender,
  contextText: segmentValue.contextText ?? segmentValue.text,
  isPlayableReference: (item) => item.reference_id !== "guard",
  hasDistinctAudio: (item) => item.reference_id === "hero-a" || item.reference_id === "hero-b",
});

assert.deepEqual(resolve(segment({ segment_type: "narration" })), { referenceId: "narrator-female", reason: "global_narrator" });
assert.deepEqual(resolve(segment({ character_id: "hero-a" })), { referenceId: "hero-a", reason: "character" });
assert.deepEqual(resolve(segment({ character_id: "woman" })), { referenceId: "narrator-female", reason: "gender_narrator" });
assert.deepEqual(resolve(segment({ contextText: "一名少年从人群中走出，随即开口。" })), { referenceId: "narrator-male", reason: "gender_narrator" });
assert.deepEqual(resolve(segment({ speaker_gender: "female" })), { referenceId: "narrator-female", reason: "gender_narrator" });
assert.deepEqual(resolve(segment()), { referenceId: "narrator-male", reason: "opposite_narrator" });
assert.deepEqual(resolve(segment({ voice_reference_id: "hero-b" })), { referenceId: "hero-b", reason: "manual" });

const extraSegment = segment({ segment_id: "extra-7", character_id: "guard", contextText: "人群中有人喊道：快走！" });
const firstExtra = resolve(extraSegment);
const repeatedExtra = resolve(extraSegment);
assert.equal(firstExtra.reason, "extra_random");
assert.deepEqual(firstExtra, repeatedExtra);
assert.ok(["hero-a", "hero-b"].includes(firstExtra.referenceId));
assert.equal(resolve(segment({ segment_id: "extra-model", speaker_kind: "extra" })).reason, "extra_random");

const jobs = [
  { job_id: "old", kind: "quality_render", status: "complete", segment_id: "segment-1", reference_id: "narrator-male", reference_audio_url: "/reference-old.wav", quality_model: "gpt_sovits_v4", output_url: "/old.wav", updated_at: "2026-01-01T00:00:00Z" },
  { job_id: "latest", kind: "quality_render", status: "complete", segment_id: "segment-1", reference_id: "narrator-male", reference_audio_url: "/reference-current.wav", quality_model: "gpt_sovits_v4", output_url: "/latest.wav", updated_at: "2026-01-02T00:00:00Z" },
  { job_id: "wrong-model", kind: "quality_render", status: "complete", segment_id: "segment-2", reference_id: "narrator-male", reference_audio_url: "/reference-current.wav", quality_model: "indextts2", output_url: "/wrong.wav", updated_at: "2026-01-02T00:00:00Z" },
  { job_id: "failed", kind: "quality_render", status: "failed", segment_id: "segment-3", reference_id: "narrator-male", reference_audio_url: "/reference-current.wav", quality_model: "gpt_sovits_v4", updated_at: "2026-01-02T00:00:00Z" },
  { job_id: "empty", kind: "quality_render", status: "complete", segment_id: "segment-3", reference_id: "narrator-male", reference_audio_url: "/reference-current.wav", quality_model: "gpt_sovits_v4", output_url: null, updated_at: "2026-01-03T00:00:00Z" },
];
const reusable = collectReusableQualityJobs({
  segments: [segment(), segment({ segment_id: "segment-2" }), segment({ segment_id: "segment-3" })],
  jobs,
  qualityModel: "gpt_sovits_v4",
  referenceIdForSegment: () => "narrator-male",
  referenceAudioUrlForSegment: () => "/reference-current.wav",
  rvcModelIdForSegment: () => null,
  rvcProfileFingerprintForSegment: () => null,
});
assert.deepEqual([...reusable], [["segment-1", "latest"]]);

const workbenchSource = readFileSync(fileURLToPath(new URL("../src/QualityWorkbench.tsx", import.meta.url)), "utf8");
const advancedSettingsSource = readFileSync(fileURLToPath(new URL("../src/AdvancedSettingsPanel.tsx", import.meta.url)), "utf8");
assert.match(workbenchSource, /startStreamingFrom\(absoluteIndex, "reuse"\)/);
assert.match(workbenchSource, /startStreamingFrom\(absoluteIndex, "restart"\)/);
assert.match(workbenchSource, /id=\{`quality-result-\$\{segment\.segment_id\}`\}/);
assert.match(workbenchSource, /locateResultSegment\(segment\.segment_id\)/);
assert.match(workbenchSource, /locateScriptSegment\(segment\.segment_id\)/);
assert.match(workbenchSource, /segmentClickTimerRef\.current = window\.setTimeout/);
assert.match(workbenchSource, /queue\.paused = true/);
assert.match(workbenchSource, /onPlay=\{pauseStreamForSinglePreview\}/);
assert.match(workbenchSource, /queue\.playIndex \+ STREAM_LOOKAHEAD_SENTENCES \+ 1/);
assert.doesNotMatch(workbenchSource, /player\.waitForPlayback\(\)/);
assert.match(workbenchSource, /后台继续加载后续句/);
assert.match(workbenchSource, /if \(reference\.audio_url\) return reference\.audio_url;[\s\S]*referenceJob\?\.status === "complete"/);
assert.match(workbenchSource, /const chapters = useMemo<ChapterGroup\[\]>/);
assert.match(workbenchSource, /const visibleSegments = activeChapter\?\.segments \?\? \[\]/);
assert.match(workbenchSource, /title="上一章"/);
assert.match(workbenchSource, /title="下一章"/);
assert.match(workbenchSource, /reprocessQualityLoudness/);
assert.match(workbenchSource, /loudnessLabel\(job\?\.loudness_metrics\)/);
assert.doesNotMatch(workbenchSource, /const PAGE_SIZE/);
assert.doesNotMatch(workbenchSource, /pageStart/);
assert.match(advancedSettingsSource, /target_lufs: -18/);
assert.match(advancedSettingsSource, /true_peak_dbtp: -1/);
assert.match(advancedSettingsSource, /max_segment_gain_db: 4/);

console.log("Quality voice routing, chapter navigation, streaming, and loudness controls verified");
