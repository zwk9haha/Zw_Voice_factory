from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


class _TeeStream:
    def __init__(self, console: TextIO, output: TextIO, lock: threading.Lock) -> None:
        self.console = console
        self.output = output
        self.lock = lock

    def write(self, value: str) -> int:
        written = self.console.write(value)
        with self.lock:
            self.output.write(value)
        return written

    def flush(self) -> None:
        self.console.flush()
        with self.lock:
            self.output.flush()

    def isatty(self) -> bool:
        return self.console.isatty()


def install_runtime_tee(workspace_root: Path, name: str) -> Path:
    runtime_root = workspace_root.resolve() / "outputs" / "logs" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in name)
    path = runtime_root / f"{safe_name}-{stamp}-{os.getpid()}.log"
    output = path.open("a", encoding="utf-8", buffering=1)
    lock = threading.Lock()
    sys.stdout = _TeeStream(sys.stdout, output, lock)
    sys.stderr = _TeeStream(sys.stderr, output, lock)
    print(f"[runtime] logging to {path}", flush=True)
    return path
