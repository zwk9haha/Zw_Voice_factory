# Zw Voice Factory Agent Instructions

- Start the application only with `Start-ZwVoice.cmd run`; it creates the visible owner console and returns.
- When starting through Codex or another sandboxed agent, run `Start-ZwVoice.cmd run` with interactive desktop/elevated execution so Windows can create the visible owner console. A sandbox-only launch can create a headless ConPTY and is not acceptable.
- Poll readiness with `Start-ZwVoice.cmd status` after starting. Never bypass the visible owner by invoking the PowerShell script directly.
- After an agent-initiated start, verify the owner `cmd.exe` window exists and has a nonzero window handle; otherwise stop the instance with `Start-ZwVoice.cmd stop` and restart it with interactive desktop access.
- Run all automated verification only with `Start-ZwVoice.cmd test`.
- Check an existing runtime with `Start-ZwVoice.cmd status` before starting another instance.
- Stop launcher-managed services only with `Start-ZwVoice.cmd stop`.
- Do not start Vite, FastAPI, GPT-SoVITS, or VoxCPM2 directly. The launcher owns model preload, console progress, and process cleanup.
