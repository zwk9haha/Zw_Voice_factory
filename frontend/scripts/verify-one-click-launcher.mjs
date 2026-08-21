import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const preparationSource = readFileSync(fileURLToPath(new URL("../src/ProjectPreparationWorkspace.tsx", import.meta.url)), "utf8");
const launcherSource = readFileSync(fileURLToPath(new URL("../../launcher/ZwVoiceLauncher/Program.cs", import.meta.url)), "utf8");
const startFactorySource = readFileSync(fileURLToPath(new URL("../../scripts/start_factory.ps1", import.meta.url)), "utf8");
const voxcpmWorkerSource = readFileSync(fileURLToPath(new URL("../../model_workers/voxcpm_server.py", import.meta.url)), "utf8");
const fastWorkerSource = readFileSync(fileURLToPath(new URL("../../model_workers/fast_tts_server.py", import.meta.url)), "utf8");
const indexWorkerSource = readFileSync(fileURLToPath(new URL("../../model_workers/indextts_server.py", import.meta.url)), "utf8");
const gptSovitsSource = readFileSync(fileURLToPath(new URL("../../models/tts_tools/gpt_sovits/api_v2.py", import.meta.url)), "utf8");

assert.match(preparationSource, /zw-continuous-quality-opened:\$\{runId\}/);
assert.match(preparationSource, /sessionStorage\.getItem\(continuousQualityNavigationKey\(runId\)\)/);
assert.match(preparationSource, /isContinuousRunRenderReady\(run\) && !hasOpenedContinuousQualityRun\(run\.run_id\)/);
assert.match(preparationSource, /markContinuousQualityRunOpened\(run\.run_id\);[\s\S]*onStageChange\("quality_render"\)/);

assert.match(launcherSource, /Primary = Color\.FromArgb\(169, 129, 212\)/);
assert.match(launcherSource, /Background = Color\.FromArgb\(15, 16, 20\)/);
assert.match(launcherSource, /Muted = Color\.FromArgb\(184, 178, 194\)/);
assert.match(launcherSource, /UseVisualStyleBackColor = false/);
assert.match(launcherSource, /private readonly ThemedButton _openButton = new\(\)/);
assert.match(launcherSource, /internal sealed class ThemedButton : Button/);
assert.match(launcherSource, /if \(Enabled\)[\s\S]*base\.OnPaint\(eventArgs\)/);
assert.match(launcherSource, /TextRenderer\.DrawText\([\s\S]*ForeColor/);
assert.match(launcherSource, /private static bool IsRoutineLog\(string message\)/);
assert.match(launcherSource, /message\.Contains\("GET \/health"/);
assert.match(launcherSource, /message\.Contains\("GET \/openapi\.json"/);
assert.match(launcherSource, /message\.Contains\("GET \/api\/tags"/);
assert.match(launcherSource, /ApplyProgress\(trimmed\);[\s\S]*if \(IsRoutineLog\(trimmed\)\)/);
assert.doesNotMatch(launcherSource, /Color\.FromArgb\(53, 200, 182\)/);
assert.match(startFactorySource, /run_quiet_ollama\.ps1/);
assert.match(voxcpmWorkerSource, /uvicorn\.run\([\s\S]*access_log=False/);
assert.match(fastWorkerSource, /uvicorn\.run\([\s\S]*access_log=False/);
assert.match(indexWorkerSource, /uvicorn\.run\([\s\S]*access_log=False/);
assert.match(gptSovitsSource, /uvicorn\.run\([\s\S]*access_log=False/);

console.log("One-click navigation and launcher palette verified");
