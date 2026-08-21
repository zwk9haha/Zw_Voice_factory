from __future__ import annotations

import argparse
import io
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from sherpa_onnx import OfflineTts, OfflineTtsConfig, OfflineTtsModelConfig, OfflineTtsVitsModelConfig

from runtime_log import install_runtime_tee


SPEAKERS = (
    {"voice_id": "suyingxue", "label": "轻量声线 1"},
    {"voice_id": "gunian", "label": "轻量声线 2"},
    {"voice_id": "fushiyu", "label": "轻量声线 3"},
    {"voice_id": "bingjiao", "label": "轻量声线 4"},
    {"voice_id": "bazong", "label": "轻量声线 5"},
)


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    voice_id: str = Field(default="suyingxue", max_length=80)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preloaded lightweight Chinese TTS service for Zw Voice Factory")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9883)
    parser.add_argument("--threads", type=int, default=2)
    return parser.parse_args()


def create_tts(model_path: Path, threads: int) -> tuple[OfflineTts, dict[str, int]]:
    model_root = model_path.resolve() / "sherpa-onnx-vits-zh-ll"
    dictionary_root = model_root / "dict"
    vits = OfflineTtsVitsModelConfig(
        model=str(model_root / "model.onnx"),
        lexicon=str(model_root / "lexicon.txt"),
        tokens=str(model_root / "tokens.txt"),
        dict_dir=str(dictionary_root),
    )
    model_config = OfflineTtsModelConfig(vits=vits, num_threads=max(1, threads), provider="cpu")
    config = OfflineTtsConfig(
        model=model_config,
        rule_fsts=",".join(str(model_root / name) for name in ("date.fst", "number.fst", "phone.fst")),
        max_num_sentences=1,
    )
    tts = OfflineTts(config)
    if tts.num_speakers != len(SPEAKERS):
        raise RuntimeError(f"轻量 TTS 说话人数量不匹配：模型返回 {tts.num_speakers}，预期 {len(SPEAKERS)}")
    return tts, {item["voice_id"]: index for index, item in enumerate(SPEAKERS)}


def create_app(model_path: Path, threads: int) -> FastAPI:
    print(f"[FastTTS] 正在加载轻量中文 TTS: {model_path.resolve()}", flush=True)
    tts, speaker_ids = create_tts(model_path, threads)
    generation_lock = threading.Lock()
    print(f"[FastTTS] 加载完成 | sample_rate={tts.sample_rate} | speakers={tts.num_speakers}", flush=True)

    application = FastAPI(title="Zw Voice Fast TTS Worker", version="1.0.0")

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ready",
            "service": "fast_tts",
            "engine": "sherpa-onnx-vits-zh-ll",
            "sample_rate": tts.sample_rate,
            "num_speakers": tts.num_speakers,
        }

    @application.get("/voices")
    def voices() -> list[dict[str, str]]:
        return list(SPEAKERS)

    @application.post("/generate")
    def generate(request: GenerateRequest) -> Response:
        speaker_id = speaker_ids.get(request.voice_id)
        if speaker_id is None:
            raise HTTPException(status_code=422, detail=f"未知的轻量 TTS 声线：{request.voice_id}")
        print(f"[FastTTS] 开始生成 | chars={len(request.text)} | voice={request.voice_id}", flush=True)
        try:
            with generation_lock:
                audio = tts.generate(request.text.strip(), sid=speaker_id, speed=request.speed)
            samples = np.asarray(audio.samples, dtype=np.float32)
            buffer = io.BytesIO()
            sf.write(buffer, samples, int(audio.sample_rate), format="WAV", subtype="PCM_16")
            print(f"[FastTTS] 生成完成 | samples={len(samples)}", flush=True)
            return Response(buffer.getvalue(), media_type="audio/wav")
        except Exception as exc:
            print(f"[FastTTS] 生成失败 | {exc}", flush=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return application


def main() -> None:
    args = parse_args()
    install_runtime_tee(Path(__file__).resolve().parents[1], "fast_tts")
    application = create_app(args.model_path, args.threads)
    uvicorn.run(application, host=args.host, port=args.port, workers=1, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
