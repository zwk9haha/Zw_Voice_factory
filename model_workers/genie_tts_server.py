from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def configure_runtime(workspace_root: Path) -> None:
    internal_root = workspace_root / "models" / "tts_tools" / "genie-tts" / "portable" / "Genie-TTS GUI" / "_internal"
    runtime_site = workspace_root / "models" / "tts_tools" / "genie-tts" / "runtime" / "python" / "Lib" / "site-packages"
    for dll_root in (internal_root, internal_root / "onnxruntime" / "capi", internal_root / "numpy.libs"):
        if dll_root.is_dir():
            os.add_dll_directory(str(dll_root))
    for import_root in (internal_root, runtime_site):
        path_value = str(import_root)
        if import_root.is_dir() and path_value in sys.path:
            sys.path.remove(path_value)
        if import_root.is_dir():
            sys.path.insert(0, path_value)


class GenieService:
    def __init__(self, workspace_root: Path, genie_data: Path) -> None:
        configure_runtime(workspace_root)
        os.environ["GENIE_DATA_DIR"] = str(genie_data.resolve())
        os.environ["HUBERT_MODEL_DIR"] = str((genie_data / "chinese-hubert-base").resolve())
        os.environ["SV_MODEL"] = str((genie_data / "speaker_encoder.onnx").resolve())
        os.environ["ROBERTA_MODEL_DIR"] = str((genie_data / "RoBERTa").resolve())
        vendor_root = Path(__file__).resolve().parent / "vendor"
        sys.path.insert(0, str(vendor_root))
        import numpy as np
        import genie_tts as genie

        self._np = np
        self._genie = genie
        self.workspace_root = workspace_root.resolve()
        self.genie_data = genie_data.resolve()
        self.generation_lock = threading.Lock()
        self.loaded_models: set[str] = set()

    def health(self) -> dict[str, object]:
        return {
            "status": "ready",
            "service": "genie_tts",
            "engine": "Genie-TTS ONNX",
            "model_cache_size": len(self.loaded_models),
            "data_dir": str(self.genie_data),
        }

    def voices(self) -> list[dict[str, str]]:
        root = self.workspace_root / "models" / "tts_tools" / "genie-tts" / "portable" / "Genie-TTS GUI" / "CharacterModels"
        result: list[dict[str, str]] = []
        if not root.is_dir():
            return result
        for model_dir in sorted(root.glob("*/**/tts_models")):
            result.append({"voice_id": f"{model_dir.parent.parent.name}/{model_dir.parent.name}", "label": model_dir.parent.name})
        return result

    def generate(self, payload: dict[str, Any]) -> bytes:
        text = self._required_text(payload, "text", 2_000)
        character_name = self._required_text(payload, "character_name", 120).lower()
        model_dir = self._project_path(payload, "model_dir", directory=True)
        reference_audio = self._project_path(payload, "reference_audio_path", directory=False)
        reference_text = self._required_text(payload, "reference_text", 1_000)
        language = self._required_text(payload, "language", 40)
        if reference_audio.suffix.lower() not in {".wav", ".flac", ".ogg", ".aiff", ".aif"}:
            raise ValueError("Genie-TTS 参考音频格式不受支持")
        with self.generation_lock:
            if character_name not in self.loaded_models:
                self._genie.load_character(character_name, model_dir, language)
                self.loaded_models.add(character_name)
            self._genie.set_reference_audio(character_name, reference_audio, reference_text, language)
            chunks = bytearray()

            async def collect() -> None:
                async for chunk in self._genie.tts_async(
                    character_name,
                    text,
                    split_sentence=bool(payload.get("split_sentence", True)),
                ):
                    chunks.extend(chunk)

            asyncio.run(collect())
        if not chunks:
            raise RuntimeError("Genie-TTS 未返回音频数据")
        samples = self._np.frombuffer(bytes(chunks), dtype=self._np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(32_000)
            wav_file.writeframes(samples.tobytes())
        return buffer.getvalue()

    def _project_path(self, payload: dict[str, Any], name: str, *, directory: bool) -> Path:
        value = self._required_text(payload, name, 2_000)
        resolved = Path(value).expanduser().resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as error:
            raise ValueError(f"{name} 必须位于项目目录内") from error
        if (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
            raise FileNotFoundError(f"{name} 不存在：{resolved}")
        return resolved

    @staticmethod
    def _required_text(payload: dict[str, Any], name: str, max_length: int) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise ValueError(f"{name} 不能为空且长度不能超过 {max_length}")
        return value.strip()


class Handler(BaseHTTPRequestHandler):
    service: GenieService

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(self.service.health())
        elif self.path == "/voices":
            self._json(self.service.voices())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/generate":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            audio = self.service.generate(payload)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
        except Exception as error:
            self._json({"detail": f"Genie-TTS 生成失败：{error}"}, status=500)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genie-TTS ONNX worker for Zw Voice Factory")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--genie-data", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9884)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    service = GenieService(args.workspace_root.resolve(), args.genie_data.resolve())
    Handler.service = service
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
