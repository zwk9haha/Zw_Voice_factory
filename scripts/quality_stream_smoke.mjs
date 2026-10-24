import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Run only through Start-ZwVoice.cmd test -QualityStreamSmoke.
// Optional: ZW_VOICE_PLAYWRIGHT_MODULE and ZW_VOICE_BROWSER_EXECUTABLE.
const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const playwrightPath = process.env.ZW_VOICE_PLAYWRIGHT_MODULE || require.resolve("playwright");
const { chromium } = await import(pathToFileURL(playwrightPath).href);
const webUrl = "http://127.0.0.1:5173";
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const reportRoot = path.join(workspace, "outputs/logs/diagnostics");
const reportPath = path.join(reportRoot, `quality-stream-smoke-${stamp}.json`);
const jobRoot = path.join(workspace, "outputs/jobs");
const jobs = readdirSync(jobRoot).filter((name) => name.endsWith(".json"))
  .map((name) => ({ name, modified: statSync(path.join(jobRoot, name)).mtimeMs }))
  .sort((left, right) => right.modified - left.modified);
let sourceJob;
let reference;
for (const { name } of jobs) {
  const job = JSON.parse(readFileSync(path.join(jobRoot, name), "utf8"));
  if (job.kind !== "quality_render" || !job.reference_audio_url || !job.reference_id || !job.project_id) continue;
  const planPath = path.join(workspace, "outputs/projects", job.project_id, "reference_plan.json");
  if (!existsSync(planPath)) continue;
  const plan = JSON.parse(readFileSync(planPath, "utf8"));
  const item = plan.items.find((candidate) => candidate.reference_id === job.reference_id);
  if (!item?.reference_text) continue;
  sourceJob = job;
  reference = item;
  break;
}
assert.ok(sourceJob && reference, "Need an existing reviewed project reference and its matching text");
const browserExecutable = process.env.ZW_VOICE_BROWSER_EXECUTABLE || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find(existsSync);
const browser = await chromium.launch({
  executablePath: browserExecutable,
  headless: true,
  args: ["--autoplay-policy=no-user-gesture-required"],
});
const report = {
  started_at: new Date().toISOString(),
  scope: "real backend inference and browser audio playback components",
  source_job_id: sourceJob.job_id,
  reference_audio_url: sourceJob.reference_audio_url,
};
mkdirSync(reportRoot, { recursive: true });
try {
  const page = await browser.newPage();
  await page.goto(webUrl, { waitUntil: "domcontentloaded" });
  report.result = await page.evaluate(async ({ referenceUrl, referenceText, projectId }) => {
    const { QualityPcmPlayer, consumeQualityPcmStream } = await import("/src/qualityAudioStream.ts");
    const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const json = async (url, options) => {
      const response = await fetch(url, { ...options, signal: AbortSignal.timeout(120_000) });
      if (!response.ok) throw new Error(`${url}: ${response.status} ${await response.text()}`);
      return response.json();
    };
    const payload = (text, quality_model) => ({
      kind: "quality_render", project_id: projectId, text, quality_model,
      reference_audio_url: referenceUrl, reference_text: referenceText,
      quality_rvc_enabled: false,
      render_options: { seed: 3407, chunk_length: 60, batch_size: 1, split_bucket: false },
    });
    const submit = (text) => json("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload(text, "gpt_sovits_v2_pro_plus")),
    });
    const complete = async (jobId) => {
      const deadline = performance.now() + 120_000;
      while (performance.now() < deadline) {
        const job = await json(`/api/jobs/${jobId}`);
        if (job.status === "complete" && job.output_url) return job;
        if (["failed", "cancelled"].includes(job.status)) throw new Error(job.error || job.message);
        await delay(250);
      }
      throw new Error(`Timed out waiting for ${jobId}`);
    };
    const prepareAudio = async (url) => {
      const audio = new Audio(url);
      audio.preload = "auto";
      const ended = new Promise((resolve, reject) => {
        audio.addEventListener("ended", () => resolve(performance.now()), { once: true });
        audio.addEventListener("error", () => reject(new Error(`Playback failed: ${audio.error?.message}`)), { once: true });
      });
      await new Promise((resolve, reject) => {
        audio.addEventListener("canplay", resolve, { once: true });
        audio.addEventListener("error", reject, { once: true });
        audio.load();
      });
      return { audio, ended };
    };
    const firstJob = await complete((await submit(
      "雨后的长街渐渐安静下来，远处的灯火依次亮起。我们沿着石板路向前走，准备迎接新的旅程。",
    )).job_id);
    const first = await prepareAudio(firstJob.output_url);
    await first.audio.play();
    const secondSubmittedAt = performance.now();
    const secondJob = await complete((await submit("我们继续向前走。")).job_id);
    const second = await prepareAudio(secondJob.output_url);
    const secondReadyAt = performance.now();
    const firstEndedAt = await first.ended;
    await second.audio.play();
    const secondStartedAt = performance.now();
    await second.ended;
    const sentencePlayback = {
      model: "gpt_sovits_v2_pro_plus",
      job_ids: [firstJob.job_id, secondJob.job_id],
      audio_urls: [firstJob.output_url, secondJob.output_url],
      durations_seconds: [first.audio.duration, second.audio.duration],
      next_generated_while_playing: secondReadyAt < firstEndedAt,
      next_generation_ms: Math.round(secondReadyAt - secondSubmittedAt),
      transition_gap_ms: Math.round(secondStartedAt - firstEndedAt),
      both_played_to_end: true,
    };
    const controller = new AbortController();
    const player = new QualityPcmPlayer();
    const pcm = { model: "gpt_sovits_v3", chunks: 0, bytes: 0, first_audio_ms: null };
    const startedAt = performance.now();
    let pauseResume = Promise.resolve();
    try {
      await player.begin();
      const response = await fetch("/api/jobs/quality-stream", {
        method: "POST", headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify(payload("雨后的街道安静下来。我们继续向前走。远处的灯火亮了。", pcm.model)),
      });
      if (!response.ok) throw new Error(`PCM stream: ${response.status} ${await response.text()}`);
      const metadata = await consumeQualityPcmStream(response, controller.signal, {
        onMetadata: (format) => { pcm.job_id = format.job_id; },
        onAudio: (format, bytes) => {
          if (pcm.first_audio_ms === null) {
            pcm.first_audio_ms = Math.round(performance.now() - startedAt);
            pauseResume = (async () => {
              await player.pause();
              if (!player.isPaused) throw new Error("PCM pause did not suspend playback");
              await delay(150);
              await player.resume();
              if (player.isPaused) throw new Error("PCM resume did not restart playback");
              pcm.pause_resume_passed = true;
            })();
          }
          if (player.append(format, bytes) === null) throw new Error("PCM audio was not scheduled");
          pcm.chunks += 1;
          pcm.bytes += bytes.length;
        },
      });
      pcm.response_end_ms = Math.round(performance.now() - startedAt);
      await pauseResume;
      player.finishStream();
      await player.waitForPlayback();
      pcm.playback_end_ms = Math.round(performance.now() - startedAt);
      pcm.duration_seconds = pcm.bytes / (metadata.sample_rate * metadata.channels * metadata.sample_width);
      pcm.sample_rate = metadata.sample_rate;
      const cached = await complete(metadata.job_id);
      pcm.audio_url = cached.output_url;
      if (!cached.streaming) throw new Error("PCM job was not recorded as streaming");
      if (pcm.chunks < 2 || pcm.bytes <= 0) throw new Error("Expected multiple real audio chunks");
      if (pcm.first_audio_ms >= pcm.response_end_ms) throw new Error("Audio only arrived after the full response");
      if (player.isActive) throw new Error("PCM player did not finish");
      pcm.played_to_end = true;
    } finally {
      controller.abort();
      await player.dispose();
    }
    return { sentence_playback: sentencePlayback, pcm_stream: pcm };
  }, {
    referenceUrl: sourceJob.reference_audio_url,
    referenceText: reference.reference_text,
    projectId: `quality-smoke-${stamp}`,
  });
  assert.ok(report.result.sentence_playback.next_generated_while_playing, "Next sentence was not ready during playback");
  assert.ok(report.result.sentence_playback.transition_gap_ms < 250, "Sentence playback had a noticeable scheduling gap");
  report.status = "passed";
  console.log(JSON.stringify(report.result, null, 2));
} catch (error) {
  report.status = "failed";
  report.error = String(error?.stack || error);
  process.exitCode = 1;
  console.error(report.error);
} finally {
  report.finished_at = new Date().toISOString();
  writeFileSync(reportPath, JSON.stringify(report, null, 2));
  await browser.close();
  console.log(`Quality stream smoke report: ${reportPath}`);
}
