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

## Development

```powershell
cd G:\Desktop\Zw_Voice_factory\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8800

cd G:\Desktop\Zw_Voice_factory\frontend
npm.cmd ci
npm.cmd run dev
```
