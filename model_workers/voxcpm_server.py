from __future__ import annotations

import argparse
import io
import threading
from pathlib import Path

import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from voxcpm import VoxCPM


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    voice_prompt: str = Field(default="自然、清晰、稳定", max_length=1_000)
    cfg_value: float = Field(default=2.0, ge=0.1, le=10)
    inference_timesteps: int = Field(default=10, ge=1, le=100)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preloaded VoxCPM2 service for Zw Voice Factory")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9881)
    parser.add_argument("--device", default="cuda")
    return parser.parse_args()


def create_app(model_path: Path, device: str) -> FastAPI:
    resolved_model_path = model_path.resolve()
    print(f"[VoxCPM2] 正在预加载模型: {resolved_model_path}", flush=True)
    model = VoxCPM.from_pretrained(
        str(resolved_model_path),
        load_denoiser=False,
        optimize=False,
        device=device,
    )
    sample_rate = int(model.tts_model.sample_rate)
    generation_lock = threading.Lock()
    print(f"[VoxCPM2] 预加载完成 | device={device} | sample_rate={sample_rate}", flush=True)

    application = FastAPI(title="Zw Voice VoxCPM2 Worker", version="1.0.0")

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ready",
            "service": "voxcpm2",
            "device": device,
            "sample_rate": sample_rate,
            "model_path": str(resolved_model_path),
        }

    @application.post("/generate")
    def generate(request: GenerateRequest) -> Response:
        controlled_text = f"({request.voice_prompt.strip()}){request.text.strip()}" if request.voice_prompt.strip() else request.text.strip()
        print(f"[VoxCPM2] 开始生成 | chars={len(request.text)}", flush=True)
        try:
            with generation_lock:
                audio = model.generate(
                    text=controlled_text,
                    cfg_value=request.cfg_value,
                    inference_timesteps=request.inference_timesteps,
                    normalize=False,
                )
            buffer = io.BytesIO()
            sf.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
            print(f"[VoxCPM2] 生成完成 | samples={len(audio)}", flush=True)
            return Response(buffer.getvalue(), media_type="audio/wav")
        except Exception as exc:
            print(f"[VoxCPM2] 生成失败 | {exc}", flush=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return application


def main() -> None:
    args = parse_args()
    if not args.model_path.is_dir():
        raise SystemExit(f"VoxCPM2 模型目录不存在: {args.model_path}")
    application = create_app(args.model_path, args.device)
    uvicorn.run(application, host=args.host, port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
