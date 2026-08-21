from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from runtime_log import install_runtime_tee


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    reference_audio_path: str = Field(min_length=1, max_length=2_000)
    emotion_text: str | None = Field(default=None, max_length=1_000)
    emotion_strength: float = Field(default=0.75, ge=0, le=1)
    chunk_length: int = Field(default=120, ge=20, le=300)
    top_k: int = Field(default=30, ge=1, le=100)
    top_p: float = Field(default=0.8, ge=0.1, le=1)
    temperature: float = Field(default=0.8, ge=0.1, le=1.5)
    repetition_penalty: float = Field(default=1.35, ge=1, le=12)
    fragment_interval: float = Field(default=0.3, ge=0.05, le=1)
    seed: int = Field(default=-1, ge=-1, le=2_147_483_647)


def create_app(model_path: Path, tool_root: Path, device: str) -> FastAPI:
    application = FastAPI(title="Zw Voice IndexTTS2 Worker", version="0.1.0")
    model_lock = threading.Lock()
    state: dict[str, object] = {"model": None, "loading": False, "error": None}

    def require_model() -> object:
        if state["model"] is not None:
            return state["model"]
        with model_lock:
            if state["model"] is not None:
                return state["model"]
            state["loading"] = True
            state["error"] = None
            try:
                sys.path.insert(0, str(tool_root))
                from indextts.infer_v2 import IndexTTS2

                print(f"[IndexTTS2] 正在按需加载模型: {model_path} | device={device}", flush=True)
                state["model"] = IndexTTS2(
                    cfg_path=str(model_path / "config.yaml"),
                    model_dir=str(model_path),
                    device=device,
                    use_fp16=device.startswith("cuda"),
                    use_cuda_kernel=False,
                    use_deepspeed=False,
                )
                print("[IndexTTS2] 模型加载完成", flush=True)
                return state["model"]
            except Exception as exc:
                state["error"] = str(exc)
                raise
            finally:
                state["loading"] = False

    @application.get("/health")
    def health() -> dict[str, object]:
        status = "loading" if state["loading"] else "ready" if state["model"] is not None else "idle"
        return {
            "status": status,
            "device": device,
            "model_loaded": state["model"] is not None,
            "error": state["error"],
        }

    @application.post("/generate")
    def generate(request: GenerateRequest) -> Response:
        reference = Path(request.reference_audio_path).resolve()
        if not reference.is_file():
            raise HTTPException(status_code=400, detail="IndexTTS2 参考音频不存在")
        output_path: Path | None = None
        try:
            model = require_model()
            if request.seed >= 0:
                import random

                import numpy
                import torch

                random.seed(request.seed)
                numpy.random.seed(request.seed)
                torch.manual_seed(request.seed)
            with tempfile.NamedTemporaryFile(prefix="zw-indextts-", suffix=".wav", delete=False) as output:
                output_path = Path(output.name)
            model.infer(
                spk_audio_prompt=str(reference),
                text=request.text,
                output_path=str(output_path),
                emo_alpha=request.emotion_strength,
                use_emo_text=bool(request.emotion_text),
                emo_text=request.emotion_text,
                use_random=request.seed < 0,
                interval_silence=round(request.fragment_interval * 1_000),
                max_text_tokens_per_segment=request.chunk_length,
                top_k=request.top_k,
                top_p=request.top_p,
                temperature=request.temperature,
                repetition_penalty=request.repetition_penalty,
                verbose=False,
            )
            audio = output_path.read_bytes()
            if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise RuntimeError("IndexTTS2 未生成有效 WAV 音频")
            return Response(content=audio, media_type="audio/wav")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            if output_path is not None:
                output_path.unlink(missing_ok=True)

    return application


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--tool-root", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9882, type=int)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    install_runtime_tee(Path(__file__).resolve().parents[1], "indextts2")
    uvicorn.run(
        create_app(arguments.model_path.resolve(), arguments.tool_root.resolve(), arguments.device),
        host=arguments.host,
        port=arguments.port,
        access_log=False,
    )
