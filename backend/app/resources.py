from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import psutil
from fastapi import APIRouter
from pydantic import BaseModel, Field


class CpuResources(BaseModel):
    percent: float = Field(ge=0, le=100)


class MemoryResources(BaseModel):
    percent: float = Field(ge=0, le=100)
    used_gb: float = Field(ge=0)
    total_gb: float = Field(gt=0)


class GpuResources(BaseModel):
    available: bool
    name: str | None = None
    percent: float | None = Field(default=None, ge=0, le=100)
    memory_percent: float | None = Field(default=None, ge=0, le=100)
    used_mb: float | None = Field(default=None, ge=0)
    total_mb: float | None = Field(default=None, ge=0)


class SystemResources(BaseModel):
    cpu: CpuResources
    memory: MemoryResources
    gpu: GpuResources
    timestamp: datetime


class ResourceMonitor:
    def snapshot(self) -> SystemResources:
        memory = psutil.virtual_memory()
        return SystemResources(
            cpu=CpuResources(percent=round(psutil.cpu_percent(interval=None), 1)),
            memory=MemoryResources(
                percent=round(memory.percent, 1),
                used_gb=round(memory.used / 1024**3, 1),
                total_gb=round(memory.total / 1024**3, 1),
            ),
            gpu=self._gpu_snapshot(),
            timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _gpu_snapshot() -> GpuResources:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                check=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            name, utilization, used, total = [part.strip() for part in completed.stdout.splitlines()[0].split(",")]
            used_mb = float(used)
            total_mb = float(total)
            return GpuResources(
                available=True,
                name=name,
                percent=float(utilization),
                memory_percent=round(used_mb / total_mb * 100, 1) if total_mb else 0,
                used_mb=used_mb,
                total_mb=total_mb,
            )
        except (FileNotFoundError, IndexError, OSError, subprocess.SubprocessError, ValueError):
            return GpuResources(available=False)


def create_resources_router(monitor: ResourceMonitor | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["system-resources"])
    resource_monitor = monitor or ResourceMonitor()

    @router.get("/resources", response_model=SystemResources)
    def resources() -> SystemResources:
        return resource_monitor.snapshot()

    return router
