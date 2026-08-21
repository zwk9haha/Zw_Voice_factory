from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Any


class RvcRuntime:
    def __init__(self, maximum_models: int = 2) -> None:
        self.maximum_models = maximum_models
        self.converters: OrderedDict[str, Any] = OrderedDict()

    def converter(self, rvc_root: Path, model_path: Path, index_path: Path) -> Any:
        cache_key = f"{model_path}|{index_path}"
        cached = self.converters.pop(cache_key, None)
        if cached is not None:
            self.converters[cache_key] = cached
            return cached

        os.environ["weight_root"] = str(model_path.parent)
        os.environ["index_root"] = str(index_path.parent)
        os.environ["outside_index_root"] = str(index_path.parent)
        os.environ["rmvpe_root"] = str(rvc_root / "assets" / "rmvpe")
        from configs.config import Config
        from infer.modules.vc.modules import VC

        converter = VC(Config())
        converter.get_vc(model_path.name)
        self.converters[cache_key] = converter
        while len(self.converters) > self.maximum_models:
            _, evicted = self.converters.popitem(last=False)
            del evicted
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        return converter


def configure_runtime(rvc_root: Path) -> None:
    os.chdir(rvc_root)
    if str(rvc_root) not in sys.path:
        sys.path.insert(0, str(rvc_root))
    sys.argv = [sys.argv[0]]
    os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")


def convert(manifest: dict[str, object], runtime: RvcRuntime) -> None:
    rvc_root = Path(str(manifest["rvc_root"])).resolve()
    model_path = Path(str(manifest["model_path"])).resolve()
    index_path = Path(str(manifest["index_path"])).resolve()
    input_path = Path(str(manifest["input_path"])).resolve()
    output_path = Path(str(manifest["output_path"])).resolve()
    profile_value = manifest.get("profile")
    profile = profile_value if isinstance(profile_value, dict) else {}

    if not model_path.is_file() or not index_path.is_file() or not input_path.is_file():
        raise RuntimeError("RVC 推理输入、模型或索引不存在")

    configure_runtime(rvc_root)
    import soundfile as sf

    converter = runtime.converter(rvc_root, model_path, index_path)
    info, result = converter.vc_single(
        0,
        str(input_path),
        int(profile.get("f0_up_key", 0)),
        None,
        str(profile.get("f0_method", "rmvpe")),
        str(index_path),
        str(index_path),
        float(profile.get("index_rate", 0.35)),
        int(profile.get("filter_radius", 3)),
        int(profile.get("resample_sr", 48_000)),
        float(profile.get("rms_mix_rate", 0.2)),
        float(profile.get("protect", 0.45)),
    )
    if result is None or result[0] is None or result[1] is None:
        raise RuntimeError(str(info))
    sample_rate, audio = result
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.wav")
    sf.write(temporary, audio, sample_rate, format="WAV", subtype="PCM_16")
    temporary.replace(output_path)


def write_response(path_value: object, payload: dict[str, object]) -> None:
    response_path = Path(str(path_value)).resolve()
    response_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = response_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(response_path)


def serve(maximum_models: int) -> None:
    runtime = RvcRuntime(maximum_models=maximum_models)
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        request: dict[str, object] = {}
        try:
            request = json.loads(stripped)
            if request.get("command") == "shutdown":
                return
            convert(request, runtime)
            write_response(
                request["response_path"],
                {"ok": True, "request_id": request.get("request_id")},
            )
        except Exception as exc:
            traceback.print_exc()
            response_path = request.get("response_path")
            if response_path:
                write_response(
                    response_path,
                    {
                        "ok": False,
                        "request_id": request.get("request_id"),
                        "error": str(exc),
                    },
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--maximum-models", type=int, default=2)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(max(1, min(4, arguments.maximum_models)))
        return
    if not arguments.manifest:
        parser.error("--manifest is required unless --serve is used")
    manifest = json.loads(Path(arguments.manifest).read_text(encoding="utf-8"))
    convert(manifest, RvcRuntime(maximum_models=1))


if __name__ == "__main__":
    main()
