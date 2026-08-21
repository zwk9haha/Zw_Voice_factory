from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RvcApplyStatus = Literal["not_requested", "bypassed", "applied", "fallback"]


@dataclass(frozen=True)
class RvcApplyResult:
    audio: bytes
    status: RvcApplyStatus
    model_id: str | None = None
    profile_fingerprint: str | None = None
    error: str | None = None
