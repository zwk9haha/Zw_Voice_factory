from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def emit(
    progress: int,
    message: str,
    *,
    stage: str | None = None,
    current_epoch: int | None = None,
    total_epochs: int | None = None,
    last_log: str | None = None,
) -> None:
    event: dict[str, object] = {
        "type": "progress",
        "progress": progress,
        "message": message,
    }
    if stage is not None:
        event["stage"] = stage
    if current_epoch is not None:
        event["current_epoch"] = current_epoch
    if total_epochs is not None:
        event["total_epochs"] = total_epochs
    if last_log:
        event["last_log"] = last_log[-500:]
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run_command(
    command: list[str],
    cwd: Path,
    *,
    on_line: Callable[[str], None] | None = None,
    allowed_return_codes: set[int] | None = None,
) -> int:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        normalized = line.rstrip()
        print(normalized, file=sys.stderr, flush=True)
        if on_line is not None:
            on_line(normalized)
    return_code = process.wait()
    if return_code not in (allowed_return_codes or {0}):
        raise RuntimeError(f"命令执行失败: {Path(command[1]).name}")
    return return_code


_TRAIN_EPOCH_PATTERN = re.compile(r"(?:Train Epoch|====>\s*Epoch):\s*(\d+)", re.IGNORECASE)
_TRAIN_BATCH_PATTERN = re.compile(r"\[(\d+(?:\.\d+)?)%\]")


def parse_training_progress(line: str, total_epochs: int) -> tuple[int, int, str] | None:
    epoch_match = _TRAIN_EPOCH_PATTERN.search(line)
    if epoch_match is None or total_epochs < 1:
        return None
    epoch = max(1, min(total_epochs, int(epoch_match.group(1))))
    batch_match = _TRAIN_BATCH_PATTERN.search(line)
    batch_percent = float(batch_match.group(1)) if batch_match else 100.0
    fraction = ((epoch - 1) + max(0.0, min(100.0, batch_percent)) / 100.0) / total_epochs
    progress = 58 + round(fraction * 24)
    return max(58, min(82, progress)), epoch, line


def prepare_dataset(manifest: dict[str, object]) -> Path:
    workspace_root = Path(str(manifest["workspace_root"])).resolve()
    datasets_root = (workspace_root / "outputs" / "rvc" / "datasets").resolve()
    dataset_root = (datasets_root / str(manifest["project_id"]) / str(manifest["experiment_name"])).resolve()
    if not dataset_root.is_relative_to(datasets_root):
        raise RuntimeError("RVC 训练集路径越出项目目录")
    if dataset_root.exists():
        shutil.rmtree(dataset_root)
    input_root = dataset_root / "input"
    input_root.mkdir(parents=True)
    for index, source_value in enumerate(manifest["input_audio_paths"]):
        source = Path(str(source_value)).resolve()
        if not source.is_file():
            continue
        suffix = source.suffix.lower() if source.suffix else ".wav"
        shutil.copy2(source, input_root / f"material-{index:04d}{suffix}")
    if not any(input_root.iterdir()):
        raise RuntimeError("没有可复制到训练集的有效音频")
    return input_root


def write_filelist(rvc_root: Path, experiment_name: str) -> Path:
    log_root = rvc_root / "logs" / experiment_name
    log_root.mkdir(parents=True, exist_ok=True)
    wave_root = log_root / "0_gt_wavs"
    feature_root = log_root / "3_feature768"
    f0_root = log_root / "2a_f0"
    f0_nsf_root = log_root / "2b-f0nsf"
    names = {
        path.stem for path in wave_root.glob("*.wav")
    } & {
        path.stem for path in feature_root.glob("*.npy")
    } & {
        path.name.removesuffix(".wav.npy") for path in f0_root.glob("*.wav.npy")
    } & {
        path.name.removesuffix(".wav.npy") for path in f0_nsf_root.glob("*.wav.npy")
    }
    if not names:
        raise RuntimeError("预处理后没有可训练的 RVC 特征")
    rows = [
        "|".join(
            [
                str(wave_root / f"{name}.wav"),
                str(feature_root / f"{name}.npy"),
                str(f0_root / f"{name}.wav.npy"),
                str(f0_nsf_root / f"{name}.wav.npy"),
                "0",
            ]
        )
        for name in names
    ]
    mute_root = rvc_root / "logs" / "mute"
    mute_row = "|".join(
        [
            str(mute_root / "0_gt_wavs" / "mute40k.wav"),
            str(mute_root / "3_feature768" / "mute.npy"),
            str(mute_root / "2a_f0" / "mute.wav.npy"),
            str(mute_root / "2b-f0nsf" / "mute.wav.npy"),
            "0",
        ]
    )
    rows.extend([mute_row, mute_row])
    random.shuffle(rows)
    filelist = log_root / "filelist.txt"
    filelist.write_text("\n".join(row.replace("\\", "\\\\") for row in rows), encoding="utf-8")
    config_path = log_root / "config.json"
    if not config_path.is_file():
        shutil.copy2(rvc_root / "configs" / "v1" / "40k.json", config_path)
    return log_root


def train_index(feature_root: Path, output_path: Path) -> None:
    import faiss
    import numpy as np

    arrays = [np.load(path) for path in sorted(feature_root.glob("*.npy"))]
    if not arrays:
        raise RuntimeError("没有可用于索引训练的 RVC 特征")
    features = np.concatenate(arrays, axis=0)
    np.random.shuffle(features)
    n_ivf = max(1, min(int(16 * math.sqrt(features.shape[0])), features.shape[0] // 39))
    index = faiss.index_factory(768, f"IVF{n_ivf},Flat")
    index_ivf = faiss.extract_index_ivf(index)
    index_ivf.nprobe = 1
    index.train(features)
    for start in range(0, features.shape[0], 8192):
        index.add(features[start : start + 8192])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.parent / f".rvc-index-{os.getpid()}.tmp"
    faiss.write_index(index, str(temporary_path))
    temporary_path.replace(output_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    arguments = parser.parse_args()
    manifest = json.loads(Path(arguments.manifest).read_text(encoding="utf-8"))
    os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
    rvc_root = Path(manifest["rvc_root"]).resolve()
    options = manifest["options"]
    experiment_name = str(manifest["experiment_name"])
    python = sys.executable

    os.environ["weight_root"] = str(rvc_root / "assets" / "weights")
    os.environ["index_root"] = str(rvc_root / "logs")
    os.environ["outside_index_root"] = str(rvc_root / "assets" / "indices")
    os.environ["rmvpe_root"] = str(rvc_root / "assets" / "rmvpe")

    emit(12, "正在整理角色参考音频与情绪素材", stage="preparing_material")
    input_root = prepare_dataset(manifest)
    log_root = rvc_root / "logs" / experiment_name
    log_root.mkdir(parents=True, exist_ok=True)

    emit(20, "正在切分、重采样并规范化训练音频", stage="preprocessing")
    run_command(
        [
            python,
            str(rvc_root / "infer" / "modules" / "train" / "preprocess.py"),
            str(input_root),
            "40000",
            str(options["process_count"]),
            str(log_root),
            "False",
            "3.7",
        ],
        rvc_root,
    )

    import torch

    gpu_id = str(options["gpu_ids"]).split("-")[0]
    cuda_available = torch.cuda.is_available()
    emit(34, "正在使用 RMVPE 提取音高", stage="extracting_pitch")
    if options["pitch_method"] == "rmvpe_gpu" and cuda_available:
        run_command(
            [
                python,
                str(rvc_root / "infer" / "modules" / "train" / "extract" / "extract_f0_rmvpe.py"),
                "1",
                "0",
                gpu_id,
                str(log_root),
                "True",
            ],
            rvc_root,
        )
    else:
        run_command(
            [
                python,
                str(rvc_root / "infer" / "modules" / "train" / "extract" / "extract_f0_print.py"),
                str(log_root),
                str(options["process_count"]),
                "rmvpe",
            ],
            rvc_root,
        )

    emit(46, "正在提取 HuBERT 音色特征", stage="extracting_features")
    run_command(
        [
            python,
            str(rvc_root / "infer" / "modules" / "train" / "extract_feature_print.py"),
            f"cuda:{gpu_id}" if cuda_available else "cpu",
            "1",
            "0",
            gpu_id,
            str(log_root),
            "v2",
            "True" if cuda_available else "False",
        ],
        rvc_root,
    )
    write_filelist(rvc_root, experiment_name)

    total_epochs = int(options["epochs"])
    emit(
        58,
        f"正在训练 RVC V2 音色模型 · 第 0/{total_epochs} 轮",
        stage="training",
        current_epoch=0,
        total_epochs=total_epochs,
    )
    train_command = [
        python,
        str(rvc_root / "infer" / "modules" / "train" / "train.py"),
        "-e",
        experiment_name,
        "-sr",
        "40k",
        "-f0",
        "1",
        "-bs",
        str(options["batch_size"]),
        "-te",
        str(options["epochs"]),
        "-se",
        str(options["save_every_epochs"]),
        "-pg",
        str(rvc_root / "assets" / "pretrained_v2" / "f0G40k.pth"),
        "-pd",
        str(rvc_root / "assets" / "pretrained_v2" / "f0D40k.pth"),
        "-l",
        "1",
        "-c",
        "1" if options["cache_gpu"] else "0",
        "-sw",
        "1",
        "-v",
        "v2",
    ]
    if cuda_available:
        train_command.extend(["-g", str(options["gpu_ids"])])
    last_reported_epoch = 0
    last_reported_batch = -1

    def report_training_line(line: str) -> None:
        nonlocal last_reported_epoch, last_reported_batch
        parsed = parse_training_progress(line, total_epochs)
        if parsed is None:
            return
        progress_value, epoch, log_line = parsed
        batch_match = _TRAIN_BATCH_PATTERN.search(log_line)
        batch = int(float(batch_match.group(1))) if batch_match else 100
        if epoch == last_reported_epoch and batch < 100 and batch < last_reported_batch + 10:
            return
        last_reported_epoch = epoch
        last_reported_batch = batch
        emit(
            progress_value,
            f"正在训练 RVC V2 音色模型 · 第 {epoch}/{total_epochs} 轮 · 当前批次 {batch}%",
            stage="training",
            current_epoch=epoch,
            total_epochs=total_epochs,
            last_log=log_line,
        )

    # RVC's train.py deliberately exits with 2333333 after writing the final checkpoint.
    run_command(
        train_command,
        rvc_root,
        on_line=report_training_line,
        allowed_return_codes={0, 2333333},
    )
    emit(
        82,
        f"RVC 训练完成 · 第 {total_epochs}/{total_epochs} 轮",
        stage="training",
        current_epoch=total_epochs,
        total_epochs=total_epochs,
    )

    emit(88, "正在构建 FAISS 检索索引", stage="building_index")
    output_index = Path(manifest["output_index_path"]).resolve()
    train_index(log_root / "3_feature768", output_index)

    weights_root = rvc_root / "assets" / "weights"
    exact_weight = weights_root / f"{experiment_name}.pth"
    candidates = list(weights_root.glob(f"{experiment_name}*.pth"))
    source_weight = exact_weight if exact_weight.is_file() else max(candidates, key=lambda path: path.stat().st_mtime)
    output_model = Path(manifest["output_model_path"]).resolve()
    output_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_weight, output_model)
    for candidate in candidates:
        candidate.unlink(missing_ok=True)
    for pattern in ("G_*.pth", "D_*.pth"):
        for checkpoint in log_root.glob(pattern):
            checkpoint.unlink(missing_ok=True)
    emit(100, "RVC 模型与索引已写入当前项目", stage="complete")


if __name__ == "__main__":
    main()
