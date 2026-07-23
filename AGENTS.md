# Zw Voice Factory Agent Instructions

- Start the application only with `Start-ZwVoice.cmd run`.
- Run all automated verification only with `Start-ZwVoice.cmd test`.
- Check an existing runtime with `Start-ZwVoice.cmd status` before starting another instance.
- Stop launcher-managed services only with `Start-ZwVoice.cmd stop`.
- Do not start Vite, FastAPI, GPT-SoVITS, or VoxCPM2 directly. The launcher owns model preload, console progress, and process cleanup.
