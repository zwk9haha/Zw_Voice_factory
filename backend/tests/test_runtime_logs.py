from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import httpx
import logging

from app.main import create_app
from app.runtime_logs import LauncherConsoleFilter


async def request(workspace_root: Path, method: str, path: str) -> httpx.Response:
    application = create_app(workspace_root)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path)


def test_runtime_logs_can_be_listed_and_read(tmp_path: Path) -> None:
    log_path = tmp_path / "outputs" / "logs" / "runtime" / "manual.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("first\nsecond\n", encoding="utf-8")

    listed = asyncio.run(request(tmp_path, "GET", "/api/logs"))
    assert listed.status_code == 200
    assert any(item["log_id"] == "runtime/manual.log" for item in listed.json())

    content = asyncio.run(request(tmp_path, "GET", "/api/logs/runtime/manual.log?tail=1"))
    assert content.status_code == 200
    assert content.text == "second"


def test_runtime_logs_export_contains_log_files(tmp_path: Path) -> None:
    log_path = tmp_path / "outputs" / "logs" / "rvc" / "job.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("worker output", encoding="utf-8")

    response = asyncio.run(request(tmp_path, "GET", "/api/logs/export"))

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "manifest.json" in archive.namelist()
        assert "rvc/job.log" in archive.namelist()


def test_launcher_console_hides_cloud_payloads_but_keeps_progress_and_errors() -> None:
    console_filter = LauncherConsoleFilter()

    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)

    assert console_filter.filter(record("[CLOUD API abc INPUT] operation=director_analysis\nlarge prompt")) is False
    assert console_filter.filter(record("[CLOUD API abc OUTPUT] operation=director_analysis\nlarge output")) is False
    assert console_filter.filter(record("[CLOUD API abc ERROR] operation=director_analysis")) is True
    assert console_filter.filter(record("[ANALYSIS project-x] 50% processing")) is True
