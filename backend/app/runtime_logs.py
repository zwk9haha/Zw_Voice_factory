from __future__ import annotations

import json
import logging
import os
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel


class RuntimeLogEntry(BaseModel):
    log_id: str
    category: str
    name: str
    size_bytes: int
    updated_at: datetime
    download_url: str


class RuntimeLogsService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.root = self.workspace_root / "outputs" / "logs"
        self.runtime_root = self.root / "runtime"
        self.rvc_root = self.root / "rvc"
        self.diagnostics_root = self.root / "diagnostics"
        for directory in (self.runtime_root, self.rvc_root, self.diagnostics_root):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create_runtime_log_path(self, name: str) -> Path:
        safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in name)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return self.runtime_root / f"{safe_name or 'runtime'}-{stamp}-{os.getpid()}.log"

    def list(self, limit: int = 200) -> list[RuntimeLogEntry]:
        files = sorted(
            (path for path in self.root.rglob("*") if path.is_file() and path.suffix.lower() in {".log", ".txt"}),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:limit]
        entries: list[RuntimeLogEntry] = []
        for path in files:
            relative = path.relative_to(self.root).as_posix()
            stat = path.stat()
            entries.append(
                RuntimeLogEntry(
                    log_id=relative,
                    category=relative.split("/", 1)[0],
                    name=path.name,
                    size_bytes=stat.st_size,
                    updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                    download_url=f"/api/logs/{relative}",
                )
            )
        return entries

    def read(self, log_id: str, tail_lines: int) -> str:
        path = self._resolve_log(log_id)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="日志文件不存在") from exc
        return "\n".join(lines[-tail_lines:])

    def export(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.diagnostics_root / f"diagnostic-{stamp}-{os.getpid()}.zip"
        with self._lock:
            log_files = [path for path in self.root.rglob("*") if path.is_file() and path.suffix.lower() in {".log", ".txt"}]
            with NamedTemporaryFile(prefix="diagnostic-", suffix=".zip", dir=self.diagnostics_root, delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(
                        "manifest.json",
                        json.dumps(
                            {
                                "generated_at": datetime.now(timezone.utc).isoformat(),
                                "workspace_root": str(self.workspace_root),
                                "files": [path.relative_to(self.root).as_posix() for path in log_files],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    for path in log_files:
                        archive.write(path, path.relative_to(self.root).as_posix())
                temporary_path.replace(target)
            finally:
                temporary_path.unlink(missing_ok=True)
        return target

    def _resolve_log(self, log_id: str) -> Path:
        relative = PurePosixPath(log_id.replace("\\", "/"))
        if not relative.parts or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise HTTPException(status_code=400, detail="日志路径无效")
        path = (self.root / Path(*relative.parts)).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.suffix.lower() not in {".log", ".txt"}:
            raise HTTPException(status_code=404, detail="日志文件不存在")
        return path


class LauncherConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        first_line = record.getMessage().splitlines()[0]
        if not first_line.startswith("[CLOUD API "):
            return True
        return " INPUT]" not in first_line and " OUTPUT]" not in first_line


def configure_runtime_logger(service: RuntimeLogsService) -> logging.Logger:
    logger = logging.getLogger("zw_voice_factory")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "zw_runtime_handler", False):
            logger.removeHandler(handler)
            handler.close()

    path = service.create_runtime_log_path("backend")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.zw_runtime_handler = True
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.zw_runtime_handler = True
    console_handler.addFilter(LauncherConsoleFilter())
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.info("backend logging initialized | log=%s", path)
    return logger


def create_runtime_logs_router(service: RuntimeLogsService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["runtime-logs"])

    @router.get("/logs", response_model=list[RuntimeLogEntry])
    def list_logs(limit: int = Query(default=200, ge=1, le=1_000)) -> list[RuntimeLogEntry]:
        return service.list(limit)

    @router.get("/logs/export")
    def export_logs() -> FileResponse:
        path = service.export()
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @router.get("/logs/{log_id:path}")
    def read_log(log_id: str, tail: int = Query(default=500, ge=1, le=20_000)) -> PlainTextResponse:
        return PlainTextResponse(service.read(log_id, tail), media_type="text/plain; charset=utf-8")

    return router
