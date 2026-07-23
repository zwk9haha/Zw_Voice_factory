# Zw Voice Factory

Zw Voice Factory is the second-generation workspace for turning a novel into a directed, multi-character listening experience.

The legacy project remains read-only at `G:\Desktop\Zw_Voice`. This project owns new application code and runtime data. Large model and reference-audio resources are physically moved here by `scripts/migrate_from_legacy.ps1`; directory junctions keep the legacy paths working.

## Product Routes

- Fast route: lightweight local TTS produces the performance base voice, then RVC supplies the character identity.
- Quality route: GPT-SoVITS uses an approved character or emotion reference directly.
- VoxCPM2 and IndexTTS2 create canonical references, emotion variants, and RVC training material; they are not the default online reader.

## Workspace

```text
backend/                 FastAPI application and domain contracts
frontend/                React + TypeScript + Vite workstation
config/                  Resource paths and runtime configuration
docs/                    Architecture, ADRs, and the next-window prompt
input/                   Imported novel text
models/                  Shared model/tool resources after migration
assets/voice_samples/    Curated reference library after migration
assets/rvc_models/       Imported or newly trained character models
outputs/                 Projects, audio cache, jobs, and reports
```

## Bootstrap

Run the migration from a Codex task whose writable workspace is `G:\Desktop\Zw_Voice_factory`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1' -Execute
```

The first command prints the plan. The second copies this seed, moves approved resources, creates legacy junctions, and writes a migration report.

## Start And Test

Use the project launcher for normal development. It starts and preloads GPT-SoVITS and VoxCPM2, then starts FastAPI and Vite. All four processes are attached to one Windows Job Object, so closing the launcher window also closes every process it started.

```powershell
.\Start-ZwVoice.cmd
```

The WebUI opens at `http://127.0.0.1:5173/`. Audio-generation progress is printed in the launcher window and shown in the WebUI. Ports `9880`, `9881`, `8800`, and `5173` must be available; the launcher never kills an unrelated process occupying one of these ports.

Run the complete verification through the same preload and process-lifetime path:

```powershell
.\Start-ZwVoice.cmd test
```

Test mode preloads both models, checks every runtime endpoint, runs backend pytest and the frontend production build, and then closes all managed processes. This is the required test entry point for the project.

## Initial Setup

The launcher expects the backend and model environments plus frontend dependencies to exist:

```powershell
cd G:\Desktop\Zw_Voice_factory\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

cd G:\Desktop\Zw_Voice_factory\frontend
npm.cmd ci
```
