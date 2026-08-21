from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from array import array
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Protocol
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .production import QualityModelId, QualityRenderOptions
from .rvc_contracts import RvcApplyResult


RvcRoute = Literal["quality", "fast"]
RvcTrainingPurpose = Literal["quality_stability", "fast_identity", "both"]
RvcModelStatus = Literal["unverified", "candidate", "approved", "rejected", "retired", "stale"]
RvcTrainingSetStatus = Literal["building", "ready", "failed"]
MINIMUM_TRAINING_SECONDS = 180
FAST_IDENTITY_MINIMUM_TRAINING_SECONDS = 480
RVC_INFERENCE_MIN_TIMEOUT_SECONDS = 120.0
DEFAULT_RVC_PREVIEW_TEXT = "夜色沿着长街慢慢沉下来，我停在路口，确认风声里没有异常，才继续向前。"
RVC_MATERIAL_TEXTS = (
    "清晨的风从长街尽头缓缓吹来，屋檐下的水珠落在石阶上。我停了一会儿，确认四周安静，才把今天的安排逐条说明清楚。",
    "窗外的云层渐渐散开，远处传来列车经过的声音。面对突然出现的变化，我仍然保持平稳呼吸，用清楚的语气给出判断。",
    "事情并没有表面看起来那么简单。先核对时间、地点和人物，再比较前后的细节，最后才能得出可靠结论，不能因为着急而省略步骤。",
    "如果道路暂时受阻，我们就换一条路线继续前进。重要的是控制节奏，留意每一次停顿，也让每个字都自然、清晰而完整。",
    "夜色落下以后，灯光沿着河岸依次亮起。我把声音放得平和一些，重新讲述事情的起因、经过与结果，不夸张，也不刻意压低。",
    "请记住一二三四五六七八九十，以及春夏秋冬、东西南北。不同音节要保持稳定，共鸣位置不要突然变化，句尾自然收住即可。",
    "门外忽然传来三声短促的敲击，我没有立刻回应，只是放慢呼吸，等走廊重新安静下来，才低声询问来人的身份。",
    "这份记录从五月十二日开始，到六月二十八日结束。所有数字、日期和姓名都要逐项核对，任何细小偏差都可能影响最后判断。",
    "他把地图摊在桌面上，先指出北面的山谷，再沿河流向东移动。计划需要随时调整，但原则和目标不能因为意外而改变。",
    "你真的决定现在出发吗？如果答案仍然是肯定的，我会准备好需要的物品，也会把途中可能遇到的风险提前说明。",
    "别急，先听我把话说完。眼前的结果虽然令人失望，却还没有到无法补救的程度，我们仍然有时间重新安排顺序。",
    "雨点密集地落在窗台上，远处偶尔传来低沉的雷声。我提高了一点音量，让每句话都能穿过嘈杂背景被清楚听见。",
    "从白纸、铅笔到玻璃杯和木盒，每件东西都放在原来的位置。只有靠近门边的椅子，似乎被人向后移动了半步。",
    "她停顿片刻，重新组织措辞，然后平静地说出自己的意见。语气并不强硬，却没有留下可以被误解的余地。",
    "现在开始倒数，十、九、八、七、六、五、四、三、二、一。声音保持均匀，不要因为接近终点就突然加快速度。",
    "这不是责备，也不是命令。我只是希望你在作出选择之前，能够看清代价，并且确认自己愿意承担接下来的结果。",
    "晨雾遮住了山脚，石阶边缘仍然带着潮气。我们放慢脚步，相互提醒前方的转弯和松动的碎石，直到视野重新清晰。",
    "会议将在下午三点十五分开始，参加者包括林先生、周女士和两位技术人员。请把资料按编号依次放在每个人面前。",
    "原先以为事情已经结束，没想到新的证据改变了整个方向。我压下惊讶，重新阅读那几行文字，确认自己没有遗漏关键细节。",
    "请分别读出这些词语：支持、持续、事实、实施；知识、指示、直视、制止。注意舌尖位置，让相近音节仍然保持区别。",
    "风从半开的窗缝钻进来，吹动桌上的纸页。我伸手压住文件，轻轻关上窗户，然后继续刚才没有说完的内容。",
    "无论对方如何催促，都不要跳过检查步骤。先确认来源，再验证内容，最后记录结论，这样才能避免把猜测当成事实。",
    "短暂的沉默之后，他笑了一下，声音比刚才轻松许多。紧张并没有完全消失，但至少已经不再妨碍正常交谈。",
    "沿着这条路一直向前，经过旧桥后向左转，再走大约两百米就能看到车站。若是天色太暗，就留意路旁的蓝色标志。",
)
RVC_BENCHMARK_TEXTS = (
    "今天的天气比预想中温和，我们可以按原定计划出发。",
    "你刚才说什么？请再重复一遍，我需要确认自己没有听错。",
    "立刻离开这里，不要回头，也不要停下来等任何人。",
    "别担心，我会留在这里，直到所有事情都得到妥善处理。",
    "他为什么会在这个时候出现，难道之前的判断全都错了吗？",
    "一想到那封迟到多年的信，她的声音便不由自主地轻了下来。",
    "远处的钟声连续响了十二下，街道重新陷入漫长的寂静。",
    "请核对编号三零七、日期九月二十一日，以及最后一行签名。",
    "虽然结果并不理想，但至少证明我们的方向没有完全错误。",
    "快一点，火势已经越过走廊，再迟几秒就来不及了！",
    "我没有生气，只是不明白你为什么始终不愿意说出真相。",
    "如果明天仍然下雨，我们就把见面地点改到车站旁的书店。",
    "窗外有人走过，脚步很轻，却在门前短暂地停了一下。",
    "这杯水有些烫，先放在桌上，等温度降下来以后再喝。",
    "从这里向南走三公里，再穿过树林，就能看见那座白色小屋。",
    "所有证据都指向同一个结论，可我仍然觉得缺少关键的一环。",
    "她笑着摇了摇头，语气轻快得像是在谈论一件无关紧要的小事。",
    "请区分支持与制止、事实与实施、知识与指示这几组词。",
    "夜风突然变强，树叶摩擦的声音几乎盖过了我们的谈话。",
    "我答应过会回来，所以无论路上发生什么，都不会改变这个决定。",
    "先把灯关掉，然后贴近墙边，等外面的脚步声彻底消失。",
    "原来如此，难怪前后两份记录中的时间始终无法对应。",
    "她压低声音问道，这件事情还有多少人知道？",
    "结束并不意味着失败，有时候它只是下一段旅程的开始。",
)
JobStatus = Literal["queued", "running", "complete", "failed", "cancelled"]
RvcTrainingStage = Literal[
    "queued",
    "preparing_material",
    "preprocessing",
    "extracting_pitch",
    "extracting_features",
    "starting_training",
    "training",
    "building_index",
    "finalizing",
    "complete",
    "failed",
    "cancelled",
]
RvcBenchmarkStatus = Literal["queued", "running", "complete", "failed"]
RvcBenchmarkDecision = Literal["pending", "approved", "rejected"]


class RvcInferenceProfile(BaseModel):
    schema_version: int = 1
    preset: Literal["conservative", "balanced", "strong", "custom"] = "balanced"
    f0_method: Literal["rmvpe"] = "rmvpe"
    f0_up_key: int = Field(default=0, ge=-24, le=24)
    index_rate: float = Field(default=0.35, ge=0, le=1)
    filter_radius: int = Field(default=3, ge=0, le=7)
    resample_sr: int = Field(default=48_000, ge=0, le=96_000)
    rms_mix_rate: float = Field(default=0.2, ge=0, le=1)
    protect: float = Field(default=0.45, ge=0, le=0.5)


def default_quality_profile() -> RvcInferenceProfile:
    return RvcInferenceProfile(
        preset="conservative",
        index_rate=0.35,
        rms_mix_rate=0.2,
        protect=0.45,
    )


def default_fast_profile() -> RvcInferenceProfile:
    return RvcInferenceProfile(
        preset="strong",
        index_rate=0.75,
        rms_mix_rate=0.3,
        protect=0.4,
    )


class RvcTrainingOptions(BaseModel):
    version: Literal["v2"] = "v2"
    sample_rate: Literal["40k"] = "40k"
    pitch_method: Literal["rmvpe_gpu", "rmvpe"] = "rmvpe_gpu"
    pitch_guidance: Literal[True] = True
    epochs: int = Field(default=20, ge=20, le=2_000)
    save_every_epochs: int = Field(default=5, ge=5, le=500)
    batch_size: int = Field(default=4, ge=1, le=32)
    process_count: int = Field(default=4, ge=1, le=32)
    gpu_ids: str = Field(default="0", pattern=r"^\d+(?:-\d+)*$")
    cache_gpu: bool = False


class RvcCharacterSettings(BaseModel):
    character_id: str
    train_enabled: bool = False
    stability_enabled: bool = False
    fast_route_enabled: bool = False
    selected_model_id: str | None = None


class RvcProjectSettings(BaseModel):
    quality_stability_enabled: bool = False
    fast_route_enabled: bool = False
    training_options: RvcTrainingOptions = Field(default_factory=RvcTrainingOptions)
    characters: list[RvcCharacterSettings] = Field(default_factory=list)


class RvcSettingsUpdate(BaseModel):
    quality_stability_enabled: bool | None = None
    fast_route_enabled: bool | None = None
    character_id: str | None = Field(default=None, max_length=120)
    train_enabled: bool | None = None
    stability_enabled: bool | None = None
    fast_character_enabled: bool | None = None
    selected_model_id: str | None = None
    training_options: RvcTrainingOptions | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "RvcSettingsUpdate":
        character_fields = {"train_enabled", "stability_enabled", "fast_character_enabled", "selected_model_id"}
        character_change = bool(character_fields & self.model_fields_set)
        if character_change and not self.character_id:
            raise ValueError("角色级 RVC 设置必须提供 character_id")
        if not character_change and self.character_id:
            raise ValueError("提供 character_id 时必须同时提交角色级设置")
        if (
            self.quality_stability_enabled is None
            and self.fast_route_enabled is None
            and self.training_options is None
            and not character_change
        ):
            raise ValueError("至少需要提交一项 RVC 设置")
        return self


class RvcModelAsset(BaseModel):
    schema_version: int = 1
    model_id: str
    label: str
    weight_path: str
    index_path: str | None = None
    source: Literal["existing", "trained"]
    character_id: str | None = None
    training_set_revision_id: str | None = None
    status: RvcModelStatus = "unverified"
    approved_routes: list[RvcRoute] = Field(default_factory=list)
    inference_profiles: dict[RvcRoute, RvcInferenceProfile] = Field(
        default_factory=lambda: {
            "quality": default_quality_profile(),
            "fast": default_fast_profile(),
        }
    )
    profile_fingerprints: dict[RvcRoute, str] = Field(default_factory=dict)
    benchmark_ids: list[str] = Field(default_factory=list)
    manifest_path: str | None = None
    size_mb: float
    updated_at: datetime


class RvcCharacterView(BaseModel):
    character_id: str
    reference_id: str
    display_name: str
    gender: Literal["male", "female", "unknown"]
    material_count: int
    material_duration_seconds: float
    training_ready: bool
    minimum_training_seconds: int = MINIMUM_TRAINING_SECONDS
    sample_audio_url: str | None = None
    train_enabled: bool = False
    stability_enabled: bool = False
    fast_route_enabled: bool = False
    selected_model_id: str | None = None
    selected_model_status: RvcModelStatus | None = None
    quality_approved: bool = False
    fast_approved: bool = False
    active_job_id: str | None = None


class RvcWorkspaceView(BaseModel):
    project_id: str
    tool_available: bool
    training_runtime_available: bool
    runtime_detail: str
    settings: RvcProjectSettings
    characters: list[RvcCharacterView]
    models: list[RvcModelAsset]
    training_sets: list[RvcTrainingSetRevision]
    benchmarks: list[RvcBenchmarkReport]
    jobs: list["RvcTrainingJob"]


class RvcTrainingRequest(BaseModel):
    character_id: str = Field(max_length=120)
    purpose: RvcTrainingPurpose = "quality_stability"
    options: RvcTrainingOptions | None = None


class RvcTrainingSetClip(BaseModel):
    clip_id: str
    source: Literal["canonical", "reference_conditioned"]
    text: str | None = None
    path: str
    sha256: str
    duration_seconds: float
    peak_dbfs: float | None = None
    clipping_ratio: float = 0.0
    silence_ratio: float = 0.0
    accepted: bool = True
    rejection_reason: str | None = None


class RvcTrainingSetRevision(BaseModel):
    schema_version: int = 1
    revision_id: str
    project_id: str
    character_id: str
    reference_id: str
    canonical_audio_version_id: str | None = None
    canonical_audio_url: str
    canonical_sha256: str
    purpose: RvcTrainingPurpose
    status: RvcTrainingSetStatus
    minimum_duration_seconds: int
    total_duration_seconds: float = 0.0
    clips: list[RvcTrainingSetClip] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class RvcPreviewRequest(BaseModel):
    character_id: str = Field(max_length=120)
    text: str = Field(default=DEFAULT_RVC_PREVIEW_TEXT, min_length=1, max_length=500)
    source: Literal["voxcpm2", "gpt_sovits_v2_pro_plus"] = "voxcpm2"


class RvcPreviewResult(BaseModel):
    preview_id: str
    project_id: str
    character_id: str
    source: Literal["voxcpm2", "gpt_sovits_v2_pro_plus"]
    source_label: str
    text: str
    base_audio_url: str
    rvc_audio_url: str
    rvc_model_id: str
    created_at: datetime


class RvcBenchmarkRequest(BaseModel):
    character_id: str = Field(max_length=120)
    model_id: str = Field(max_length=120)
    route: RvcRoute
    fast_voice_id: str = Field(default="suyingxue", max_length=80)


class RvcBenchmarkSample(BaseModel):
    sample_id: str
    text: str
    base_audio_url: str
    rvc_audio_url: str
    duration_ratio: float
    base_peak_dbfs: float | None = None
    rvc_peak_dbfs: float | None = None
    rvc_clipping_ratio: float = 0.0
    rvc_silence_ratio: float = 0.0
    automatic_pass: bool


class RvcBenchmarkReport(BaseModel):
    schema_version: int = 1
    benchmark_id: str
    project_id: str
    character_id: str
    model_id: str
    route: RvcRoute
    canonical_audio_version_id: str | None = None
    canonical_sha256: str | None = None
    inference_profile: RvcInferenceProfile | None = None
    profile_fingerprint: str | None = None
    status: RvcBenchmarkStatus
    progress: int = Field(ge=0, le=100)
    message: str
    automatic_pass: bool = False
    decision: RvcBenchmarkDecision = "pending"
    preference_percent: float | None = Field(default=None, ge=0, le=100)
    identity_improved: bool | None = None
    intelligibility_preserved: bool | None = None
    expression_preserved: bool | None = None
    reviewer_notes: str = Field(default="", max_length=2_000)
    samples: list[RvcBenchmarkSample] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "当前自动门仅覆盖时长、削波、静音与输出完整性；说话人嵌入和 ASR CER 将在后续指标版本加入。",
            "批准仍要求用户完成整组盲听并确认身份、可懂度与表现力。",
        ]
    )
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class RvcBenchmarkReviewRequest(BaseModel):
    approved: bool
    preference_percent: float = Field(ge=0, le=100)
    identity_improved: bool
    intelligibility_preserved: bool
    expression_preserved: bool
    notes: str = Field(default="", max_length=2_000)


class RvcProfileUpdateRequest(BaseModel):
    profile: RvcInferenceProfile


class RvcTrainingJob(BaseModel):
    job_id: str
    project_id: str
    character_id: str
    display_name: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    stage: RvcTrainingStage = "queued"
    current_epoch: int | None = Field(default=None, ge=0)
    total_epochs: int | None = Field(default=None, ge=1)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0)
    last_log: str | None = None
    options: RvcTrainingOptions
    material_count: int
    material_duration_seconds: float
    purpose: RvcTrainingPurpose = "quality_stability"
    training_set_revision_id: str | None = None
    model_id: str | None = None
    log_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RvcTrainingSpec:
    job_id: str
    project_id: str
    character_id: str
    display_name: str
    experiment_name: str
    workspace_root: Path
    rvc_root: Path
    input_audio_paths: tuple[Path, ...]
    output_model_path: Path
    output_index_path: Path
    options: RvcTrainingOptions
    purpose: RvcTrainingPurpose = "quality_stability"
    training_set_revision_id: str = ""
    log_id: str = ""


@dataclass(frozen=True)
class RvcTrainingArtifact:
    model_path: Path
    index_path: Path


class RvcTrainingRunner(Protocol):
    def run(
        self,
        spec: RvcTrainingSpec,
        progress: Callable[..., None],
        is_cancelled: Callable[[], bool],
    ) -> RvcTrainingArtifact: ...

    def cancel(self, job_id: str) -> None: ...

    def close(self) -> None: ...


class RvcInferenceRunner(Protocol):
    def run(
        self,
        model_path: Path,
        index_path: Path,
        input_path: Path,
        output_path: Path,
        profile: RvcInferenceProfile,
    ) -> None: ...

    def close(self) -> None: ...


class RvcPreviewGateway(Protocol):
    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes: ...

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes: ...

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: QualityModelId,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> bytes: ...


class RvcProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class WorkerRvcTrainingRunner:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.rvc_root = workspace_root / "models" / "vc_tools" / "rvc-webui"
        self.worker_path = workspace_root / "model_workers" / "rvc_train_worker.py"
        self.python_path = self._find_python()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        required = (
            self.rvc_root / "infer" / "modules" / "train" / "preprocess.py",
            self.rvc_root / "assets" / "hubert" / "hubert_base.pt",
            self.rvc_root / "assets" / "rmvpe" / "rmvpe.pt",
            self.worker_path,
        )
        return self.python_path is not None and all(path.is_file() for path in required)

    @property
    def detail(self) -> str:
        if self.available:
            return "RVC V2 / 40k / RMVPE 训练运行时可用"
        if self.python_path is None:
            return "未找到包含 RVC 依赖的项目内 Python 运行时"
        return "RVC 训练脚本或基础模型不完整"

    def _find_python(self) -> Path | None:
        configured = os.getenv("RVC_PYTHON")
        candidates = [
            Path(configured) if configured else None,
            self.rvc_root / "runtime" / "python.exe",
            self.rvc_root / ".venv" / "Scripts" / "python.exe",
            self.workspace_root / "models" / "tts_tools" / "gpt_sovits" / ".venv" / "Scripts" / "python.exe",
        ]
        return next((path.resolve() for path in candidates if path is not None and path.is_file()), None)

    def run(
        self,
        spec: RvcTrainingSpec,
        progress: Callable[..., None],
        is_cancelled: Callable[[], bool],
    ) -> RvcTrainingArtifact:
        if not self.available or self.python_path is None:
            raise RuntimeError(self.detail)
        manifest_root = self.workspace_root / "outputs" / "rvc" / "manifests"
        manifest_root.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_root / f"{spec.job_id}.json"
        log_path = self.workspace_root / "outputs" / "logs" / Path(spec.log_id or f"rvc/{spec.job_id}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._append_log(log_path, f"RVC training started | job={spec.job_id} | cwd={self.rvc_root}")
        manifest_path.write_text(
            json.dumps(
                {
                    "job_id": spec.job_id,
                    "project_id": spec.project_id,
                    "character_id": spec.character_id,
                    "display_name": spec.display_name,
                    "experiment_name": spec.experiment_name,
                    "workspace_root": str(spec.workspace_root),
                    "rvc_root": str(spec.rvc_root),
                    "input_audio_paths": [str(path) for path in spec.input_audio_paths],
                    "output_model_path": str(spec.output_model_path),
                    "output_index_path": str(spec.output_index_path),
                    "purpose": spec.purpose,
                    "training_set_revision_id": spec.training_set_revision_id,
                    "options": spec.options.model_dump(mode="json"),
                    "log_id": spec.log_id or f"rvc/{spec.job_id}.log",
                    "log_path": str(log_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        environment = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
            "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
        }
        command = [str(self.python_path), str(self.worker_path), "--manifest", str(manifest_path)]
        self._append_log(log_path, f"command={subprocess.list2cmdline(command)}")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.workspace_root),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except Exception as exc:
            self._append_log(log_path, f"failed to start worker: {exc!r}")
            raise
        with self._lock:
            self._processes[spec.job_id] = process
        tail: list[str] = []
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                if is_cancelled():
                    self._terminate_tree(process)
                    raise RuntimeError("RVC 训练已取消")
                line = raw_line.strip()
                if not line:
                    continue
                self._append_log(log_path, line)
                tail.append(line)
                tail = tail[-80:]
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "progress":
                    progress(
                        int(event["progress"]),
                        str(event["message"]),
                        str(event["stage"]) if event.get("stage") else None,
                        self._optional_int(event.get("current_epoch")),
                        self._optional_int(event.get("total_epochs")),
                        str(event["last_log"]) if event.get("last_log") else None,
                    )
            return_code = process.wait()
            if return_code != 0:
                details = "\n".join(tail[-40:])[-12_000:]
                self._append_log(log_path, f"worker exited with code={return_code}")
                raise RuntimeError(f"RVC training worker failed with exit code {return_code}\n{details}")
            if not spec.output_model_path.is_file() or not spec.output_index_path.is_file():
                self._append_log(log_path, "worker exited successfully but model/index artifacts are missing")
                raise RuntimeError("RVC 训练结束但未生成完整的模型与索引")
            self._append_log(log_path, "RVC training completed")
            return RvcTrainingArtifact(spec.output_model_path, spec.output_index_path)
        finally:
            with self._lock:
                self._processes.pop(spec.job_id, None)

    @staticmethod
    def _append_log(path: Path, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as output:
            output.write(f"{timestamp} {message}\n")

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def cancel(self, job_id: str) -> None:
        with self._lock:
            process = self._processes.get(job_id)
        if process is not None:
            self._terminate_tree(process)

    def close(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            self._terminate_tree(process)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.terminate()


class WorkerRvcInferenceRunner:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.rvc_root = workspace_root / "models" / "vc_tools" / "rvc-webui"
        self.worker_path = workspace_root / "model_workers" / "rvc_infer_worker.py"
        self.python_path = WorkerRvcTrainingRunner(workspace_root).python_path
        self.runtime_root = workspace_root / "outputs" / "rvc" / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.log_path = workspace_root / "outputs" / "logs" / "runtime" / "rvc-inference.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._log_output: object | None = None

    @property
    def available(self) -> bool:
        return self.python_path is not None and self.worker_path.is_file()

    def run(
        self,
        model_path: Path,
        index_path: Path,
        input_path: Path,
        output_path: Path,
        profile: RvcInferenceProfile,
    ) -> None:
        if not self.available or self.python_path is None:
            raise RuntimeError("RVC 推理运行时不可用")
        request_id = uuid.uuid4().hex[:12]
        response_path = self.runtime_root / f"{request_id}.json"
        try:
            with self._lock:
                process = self._ensure_process()
                if process.stdin is None:
                    raise RuntimeError("RVC 常驻 Worker 输入管道不可用")
                command = {
                    "request_id": request_id,
                    "response_path": str(response_path),
                    "rvc_root": str(self.rvc_root),
                    "model_path": str(model_path),
                    "index_path": str(index_path),
                    "input_path": str(input_path),
                    "output_path": str(output_path),
                    "profile": profile.model_dump(mode="json"),
                }
                process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
                process.stdin.flush()
                timeout_seconds = max(RVC_INFERENCE_MIN_TIMEOUT_SECONDS, self._wav_duration(input_path) * 4)
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    if response_path.is_file():
                        payload = json.loads(response_path.read_text(encoding="utf-8"))
                        if not payload.get("ok"):
                            raise RuntimeError(str(payload.get("error") or "RVC Worker 返回失败"))
                        if not output_path.is_file():
                            raise RuntimeError("RVC Worker 未写入输出音频")
                        return
                    if process.poll() is not None:
                        self._stop_process()
                        raise RuntimeError(f"RVC 常驻 Worker 异常退出，代码 {process.returncode}")
                    time.sleep(0.05)
                self._stop_process()
                raise TimeoutError(f"RVC 推理超过 {timeout_seconds:.1f} 秒，Worker 已重启")
        finally:
            response_path.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            self._stop_process()

    @property
    def detail(self) -> str:
        state = "运行中" if self._process is not None and self._process.poll() is None else "按需启动"
        return f"RVC 常驻推理 Worker {state} · LRU 2 模型"

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._stop_process()
        environment = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
            "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
        }
        self._log_output = self.log_path.open("a", encoding="utf-8")
        self._process = subprocess.Popen(
            [str(self.python_path), str(self.worker_path), "--serve", "--maximum-models", "2"],
            cwd=str(self.workspace_root),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=self._log_output,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return self._process

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write('{"command":"shutdown"}\n')
                    process.stdin.flush()
                process.wait(timeout=3)
            except Exception:
                process.kill()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_output is not None:
            try:
                self._log_output.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._log_output = None

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() / max(1, audio.getframerate())
        except (OSError, EOFError, wave.Error):
            return 0.0


class RvcService:
    def __init__(
        self,
        workspace_root: Path,
        runner: RvcTrainingRunner | None = None,
        inference_runner: RvcInferenceRunner | None = None,
        material_generator: Callable[[str, str], bytes] | None = None,
        preview_gateway: RvcPreviewGateway | None = None,
        runtime_logger: logging.Logger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.rvc_root = self.workspace_root / "models" / "vc_tools" / "rvc-webui"
        self.output_root = self.workspace_root / "outputs" / "rvc"
        self.jobs_root = self.output_root / "jobs"
        self.models_root = self.output_root / "models"
        self.model_manifests_root = self.output_root / "model_manifests"
        self.training_sets_root = self.output_root / "training_sets"
        self.benchmarks_root = self.output_root / "benchmarks"
        self.previews_root = self.output_root / "previews"
        self.benchmark_audio_root = self.previews_root / "benchmarks"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.models_root.mkdir(parents=True, exist_ok=True)
        self.model_manifests_root.mkdir(parents=True, exist_ok=True)
        self.training_sets_root.mkdir(parents=True, exist_ok=True)
        self.benchmarks_root.mkdir(parents=True, exist_ok=True)
        self.previews_root.mkdir(parents=True, exist_ok=True)
        self.benchmark_audio_root.mkdir(parents=True, exist_ok=True)
        self.runner = runner or WorkerRvcTrainingRunner(self.workspace_root)
        self.inference_runner = inference_runner or WorkerRvcInferenceRunner(self.workspace_root)
        self.material_generator = material_generator
        self.preview_gateway = preview_gateway
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self._lock = threading.RLock()
        self._gpu_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zw-rvc-training")
        self._benchmark_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zw-rvc-benchmark")
        self._jobs: dict[str, RvcTrainingJob] = {}
        self._futures: dict[str, Future[None]] = {}
        self._benchmarks: dict[str, RvcBenchmarkReport] = {}
        self._cancelled: set[str] = set()
        self._load_jobs()
        self._load_benchmarks()

    @property
    def gpu_lock(self) -> threading.RLock:
        return self._gpu_lock

    def close(self) -> None:
        with self._lock:
            active_ids = [job_id for job_id, job in self._jobs.items() if job.status in {"queued", "running"}]
            self._cancelled.update(active_ids)
        for job_id in active_ids:
            self.runner.cancel(job_id)
        self.runner.close()
        self.inference_runner.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._benchmark_executor.shutdown(wait=False, cancel_futures=True)

    def apply_quality_stability(
        self,
        project_id: str,
        character_id: str | None,
        reference_id: str | None,
        audio: bytes,
    ) -> RvcApplyResult:
        return self._apply_route_rvc(project_id, character_id, reference_id, audio, "quality")

    def apply_fast_route(
        self,
        project_id: str,
        character_id: str | None,
        reference_id: str | None,
        audio: bytes,
    ) -> RvcApplyResult:
        return self._apply_route_rvc(project_id, character_id, reference_id, audio, "fast")

    def _apply_route_rvc(
        self,
        project_id: str,
        character_id: str | None,
        reference_id: str | None,
        audio: bytes,
        route: Literal["quality", "fast"],
    ) -> RvcApplyResult:
        settings = self._read_settings(project_id)
        route_enabled = settings.quality_stability_enabled if route == "quality" else settings.fast_route_enabled
        if not route_enabled:
            return RvcApplyResult(audio=audio, status="not_requested")
        binding_id = self._resolve_binding_character_id(project_id, character_id, reference_id)
        binding = next((item for item in settings.characters if item.character_id == binding_id), None)
        binding_enabled = binding.stability_enabled if route == "quality" and binding else binding.fast_route_enabled if binding else False
        if binding is None or not binding_enabled or not binding.selected_model_id:
            return RvcApplyResult(audio=audio, status="bypassed")
        model = next((item for item in self._scan_models() if item.model_id == binding.selected_model_id), None)
        if model is None or model.index_path is None:
            return RvcApplyResult(
                audio=audio,
                status="fallback",
                model_id=binding.selected_model_id,
                error="RVC 路线模型或索引已不存在，请重新绑定",
            )
        if not self._route_approval_current(project_id, binding_id, model, route):
            return RvcApplyResult(
                audio=audio,
                status="fallback",
                model_id=model.model_id,
                error="RVC 路线批准已过期，请确认标准参考和参数后重新运行基准",
            )
        model_path = self._resolve_workspace_path(model.weight_path)
        index_path = self._resolve_workspace_path(model.index_path)
        if model_path is None or index_path is None:
            return RvcApplyResult(
                audio=audio,
                status="fallback",
                model_id=model.model_id,
                error="RVC 路线模型路径无效",
            )
        inference_root = self.output_root / "inference"
        inference_root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        input_path = inference_root / f"{token}-input.wav"
        output_path = inference_root / f"{token}-output.wav"
        try:
            input_path.write_bytes(audio)
            profile = model.inference_profiles.get(
                route,
                default_quality_profile() if route == "quality" else default_fast_profile(),
            )
            profile_fingerprint = self._profile_fingerprint(profile)
            try:
                with self._gpu_lock:
                    self.inference_runner.run(model_path, index_path, input_path, output_path, profile)
                result = output_path.read_bytes()
                if len(result) < 44 or result[:4] != b"RIFF" or result[8:12] != b"WAVE":
                    raise RuntimeError("RVC 路线未返回有效 WAV")
                return RvcApplyResult(
                    audio=result,
                    status="applied",
                    model_id=model.model_id,
                    profile_fingerprint=profile_fingerprint,
                )
            except Exception as exc:
                self.runtime_logger.exception(
                    "[RVC FALLBACK] route=%s project=%s character=%s model=%s",
                    route,
                    project_id,
                    binding_id,
                    model.model_id,
                )
                return RvcApplyResult(
                    audio=audio,
                    status="fallback",
                    model_id=model.model_id,
                    profile_fingerprint=profile_fingerprint,
                    error=str(exc),
                )
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def create_preview(self, project_id: str, request: RvcPreviewRequest) -> RvcPreviewResult:
        self._project_root(project_id)
        if self.preview_gateway is None:
            raise RvcProblem(409, "试听音频生成服务不可用")
        material = next(
            (item for item in self._character_materials(project_id) if item["character_id"] == request.character_id),
            None,
        )
        if material is None:
            raise RvcProblem(404, "RVC 角色不存在或尚未生成参考计划")
        settings = self._read_settings(project_id)
        binding = next((item for item in settings.characters if item.character_id == request.character_id), None)
        if binding is None or not binding.selected_model_id:
            raise RvcProblem(409, "请先为当前角色绑定包含 PTH 与 INDEX 的 RVC 模型")
        model = next((item for item in self._scan_models() if item.model_id == binding.selected_model_id), None)
        if model is None or model.index_path is None:
            raise RvcProblem(409, "当前角色绑定的 RVC 模型缺少 PTH 或 INDEX")
        model_path = self._resolve_workspace_path(model.weight_path)
        index_path = self._resolve_workspace_path(model.index_path)
        if model_path is None or index_path is None:
            raise RvcProblem(409, "当前角色绑定的 RVC 模型文件不存在")

        reference_paths = material["paths"]
        if request.source == "gpt_sovits_v2_pro_plus" and not reference_paths:
            raise RvcProblem(409, "GPT-SoVITS V2 Pro Plus 试听需要当前角色的参考音频")

        preview_id = uuid.uuid4().hex[:12]
        preview_root = self.previews_root / project_id / self._training_slug(request.character_id)
        preview_root.mkdir(parents=True, exist_ok=True)
        base_path = preview_root / f"{preview_id}-base.wav"
        rvc_path = preview_root / f"{preview_id}-rvc.wav"
        base_temporary = base_path.with_suffix(".wav.tmp")
        rvc_temporary = rvc_path.with_suffix(".wav.tmp")
        try:
            with self._gpu_lock:
                if request.source == "voxcpm2":
                    base_audio = self.preview_gateway.generate_voxcpm(request.text, str(material["voice_prompt"]))
                    source_label = "VoxCPM2"
                else:
                    base_audio = self.preview_gateway.generate_quality(
                        request.text,
                        reference_paths[0],
                        "gpt_sovits_v2_pro_plus",
                        None,
                        QualityRenderOptions(),
                    )
                    source_label = "GPT-SoVITS V2 Pro Plus"
                self._require_wav(base_audio, source_label)
                base_temporary.write_bytes(base_audio)
                base_temporary.replace(base_path)
                self.inference_runner.run(
                    model_path,
                    index_path,
                    base_path,
                    rvc_temporary,
                    model.inference_profiles.get("quality", default_quality_profile()),
                )
                rvc_audio = rvc_temporary.read_bytes()
                self._require_wav(rvc_audio, "RVC")
                rvc_temporary.replace(rvc_path)
            for old_preview in preview_root.glob("*.wav"):
                if old_preview not in {base_path, rvc_path}:
                    old_preview.unlink(missing_ok=True)
        except RvcProblem:
            base_path.unlink(missing_ok=True)
            rvc_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            base_path.unlink(missing_ok=True)
            rvc_path.unlink(missing_ok=True)
            self.runtime_logger.exception("[RVC PREVIEW] generation failed | project=%s character=%s", project_id, request.character_id)
            raise RvcProblem(502, f"RVC A/B 试听生成失败：{exc}") from exc
        finally:
            base_temporary.unlink(missing_ok=True)
            rvc_temporary.unlink(missing_ok=True)

        self.runtime_logger.info(
            "[RVC PREVIEW] complete | project=%s character=%s source=%s model=%s",
            project_id,
            request.character_id,
            request.source,
            model.model_id,
        )
        media_root = f"/media/outputs/rvc-previews/{project_id}/{self._training_slug(request.character_id)}"
        return RvcPreviewResult(
            preview_id=preview_id,
            project_id=project_id,
            character_id=request.character_id,
            source=request.source,
            source_label=source_label,
            text=request.text,
            base_audio_url=f"{media_root}/{base_path.name}",
            rvc_audio_url=f"{media_root}/{rvc_path.name}",
            rvc_model_id=model.model_id,
            created_at=datetime.now(timezone.utc),
        )

    def submit_benchmark(self, project_id: str, request: RvcBenchmarkRequest) -> RvcBenchmarkReport:
        material = next(
            (item for item in self._character_materials(project_id) if item["character_id"] == request.character_id),
            None,
        )
        if material is None or not isinstance(material.get("canonical_path"), Path):
            raise RvcProblem(409, "基准测试需要当前角色已确认的标准参考音频")
        model = next((item for item in self._scan_models() if item.model_id == request.model_id), None)
        if model is None or not model.index_path:
            raise RvcProblem(404, "基准测试需要明确绑定 PTH 与 INDEX 的 RVC 模型")
        if model.character_id and model.character_id != request.character_id:
            raise RvcProblem(409, "该 RVC 模型属于其他角色")
        if any(
            report.project_id == project_id
            and report.character_id == request.character_id
            and report.model_id == request.model_id
            and report.route == request.route
            and report.status in {"queued", "running"}
            for report in self._benchmarks.values()
        ):
            raise RvcProblem(409, "该角色与模型已有同路线基准正在运行")
        now = datetime.now(timezone.utc)
        benchmark_id = f"rvcbench-{uuid.uuid4().hex[:12]}"
        profile = model.inference_profiles.get(
            request.route,
            default_quality_profile() if request.route == "quality" else default_fast_profile(),
        )
        report = RvcBenchmarkReport(
            benchmark_id=benchmark_id,
            project_id=project_id,
            character_id=request.character_id,
            model_id=request.model_id,
            route=request.route,
            canonical_audio_version_id=(
                str(material["canonical_version_id"])
                if material.get("canonical_version_id")
                else None
            ),
            canonical_sha256=self._sha256(material["canonical_path"]),
            inference_profile=profile.model_copy(deep=True),
            profile_fingerprint=self._profile_fingerprint(profile),
            status="queued",
            progress=0,
            message="已进入 RVC 基准队列",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._benchmarks[benchmark_id] = report
            self._persist_benchmark(report)
        self._benchmark_executor.submit(self._run_benchmark, report, request, material, model)
        return report.model_copy(deep=True)

    def get_benchmark(self, benchmark_id: str) -> RvcBenchmarkReport:
        with self._lock:
            report = self._benchmarks.get(benchmark_id)
            if report is None:
                raise RvcProblem(404, "RVC 基准任务不存在")
            return report.model_copy(deep=True)

    def review_benchmark(
        self,
        benchmark_id: str,
        request: RvcBenchmarkReviewRequest,
    ) -> RvcWorkspaceView:
        with self._lock:
            report = self._benchmarks.get(benchmark_id)
            if report is None:
                raise RvcProblem(404, "RVC 基准任务不存在")
            if report.status != "complete":
                raise RvcProblem(409, "RVC 基准尚未完成")
            if report.decision != "pending":
                raise RvcProblem(409, "RVC 基准已经完成审核，不能重复提交结论")
            if request.approved and (
                not report.automatic_pass
                or request.preference_percent < 70
                or not request.identity_improved
                or not request.intelligibility_preserved
                or not request.expression_preserved
            ):
                raise RvcProblem(409, "批准要求自动质检通过、盲听偏好不少于 70%，且身份、可懂度和表现力均通过")
            decision: RvcBenchmarkDecision = "approved" if request.approved else "rejected"
            reviewed = report.model_copy(
                update={
                    "decision": decision,
                    "preference_percent": request.preference_percent,
                    "identity_improved": request.identity_improved,
                    "intelligibility_preserved": request.intelligibility_preserved,
                    "expression_preserved": request.expression_preserved,
                    "reviewer_notes": request.notes,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._benchmarks[benchmark_id] = reviewed
            self._persist_benchmark(reviewed)
            model = next((item for item in self._scan_models() if item.model_id == report.model_id), None)
            if model is None or not model.index_path:
                raise RvcProblem(404, "基准对应的 RVC 模型已经丢失")
            current_profile = model.inference_profiles.get(
                report.route,
                default_quality_profile() if report.route == "quality" else default_fast_profile(),
            )
            if report.profile_fingerprint != self._profile_fingerprint(current_profile):
                raise RvcProblem(409, "RVC 路线参数已在基准后变化，请重新运行基准")
            material = next(
                (
                    item
                    for item in self._character_materials(report.project_id)
                    if item["character_id"] == report.character_id
                ),
                None,
            )
            canonical_path = material.get("canonical_path") if material else None
            if not isinstance(canonical_path, Path) or report.canonical_sha256 != self._sha256(canonical_path):
                raise RvcProblem(409, "标准参考已在基准后变化，请重新训练或重新运行基准")
            routes = list(dict.fromkeys([*model.approved_routes, report.route])) if request.approved else list(model.approved_routes)
            status_value: RvcModelStatus = "approved" if routes else "rejected"
            persisted = model.model_copy(
                update={
                    "character_id": report.character_id,
                    "status": status_value,
                    "approved_routes": routes,
                    "benchmark_ids": list(dict.fromkeys([*model.benchmark_ids, report.benchmark_id])),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._write_model_manifest(persisted)
        return self.workspace(report.project_id)

    def _run_benchmark(
        self,
        original: RvcBenchmarkReport,
        request: RvcBenchmarkRequest,
        material: dict[str, object],
        model: RvcModelAsset,
    ) -> None:
        benchmark_root = self.benchmark_audio_root / original.project_id / original.benchmark_id
        benchmark_root.mkdir(parents=True, exist_ok=True)
        canonical_path = material.get("canonical_path")
        model_path = self._resolve_workspace_path(model.weight_path)
        index_path = self._resolve_workspace_path(model.index_path or "")
        if not isinstance(canonical_path, Path) or model_path is None or index_path is None:
            self._update_benchmark(
                original.benchmark_id,
                status="failed",
                message="RVC 基准输入已丢失",
                error="标准参考、模型或索引不存在",
            )
            return
        if self.preview_gateway is None:
            self._update_benchmark(
                original.benchmark_id,
                status="failed",
                message="RVC 基准生成器不可用",
                error="模型网关未连接",
            )
            return
        samples: list[RvcBenchmarkSample] = []
        profile = original.inference_profile or model.inference_profiles.get(
            request.route,
            default_quality_profile() if request.route == "quality" else default_fast_profile(),
        )
        try:
            self._update_benchmark(original.benchmark_id, status="running", progress=1, message="正在生成盲听基准")
            for index, text in enumerate(RVC_BENCHMARK_TEXTS, start=1):
                sample_id = f"sample-{index:02d}"
                base_path = benchmark_root / f"{sample_id}-base.wav"
                rvc_path = benchmark_root / f"{sample_id}-rvc.wav"
                temporary = rvc_path.with_suffix(".wav.tmp")
                with self._gpu_lock:
                    if request.route == "quality":
                        base_audio = self.preview_gateway.generate_quality(
                            text,
                            canonical_path,
                            "gpt_sovits_v2_pro_plus",
                            None,
                            QualityRenderOptions(),
                        )
                    else:
                        base_audio = self.preview_gateway.generate_fast(text, request.fast_voice_id, 1.0)
                    self._require_wav(base_audio, "RVC 基准基础渲染")
                    base_path.write_bytes(base_audio)
                    self.inference_runner.run(model_path, index_path, base_path, temporary, profile)
                rvc_audio = temporary.read_bytes()
                self._require_wav(rvc_audio, "RVC 基准后处理")
                temporary.replace(rvc_path)
                base_metrics = self._training_clip(base_path, "canonical", text)
                rvc_metrics = self._training_clip(rvc_path, "reference_conditioned", text)
                duration_ratio = round(
                    rvc_metrics.duration_seconds / max(0.001, base_metrics.duration_seconds),
                    4,
                )
                sample_pass = bool(
                    rvc_metrics.accepted
                    and 0.85 <= duration_ratio <= 1.15
                    and rvc_metrics.clipping_ratio <= 0.001
                    and rvc_metrics.silence_ratio <= 0.8
                )
                media_root = (
                    f"/media/outputs/rvc-previews/benchmarks/{original.project_id}/"
                    f"{original.benchmark_id}"
                )
                samples.append(
                    RvcBenchmarkSample(
                        sample_id=sample_id,
                        text=text,
                        base_audio_url=f"{media_root}/{base_path.name}",
                        rvc_audio_url=f"{media_root}/{rvc_path.name}",
                        duration_ratio=duration_ratio,
                        base_peak_dbfs=base_metrics.peak_dbfs,
                        rvc_peak_dbfs=rvc_metrics.peak_dbfs,
                        rvc_clipping_ratio=rvc_metrics.clipping_ratio,
                        rvc_silence_ratio=rvc_metrics.silence_ratio,
                        automatic_pass=sample_pass,
                    )
                )
                self._update_benchmark(
                    original.benchmark_id,
                    progress=min(98, round(index / len(RVC_BENCHMARK_TEXTS) * 98)),
                    message=f"正在生成基准句 {index}/{len(RVC_BENCHMARK_TEXTS)}",
                    samples=samples,
                )
            automatic_pass = all(sample.automatic_pass for sample in samples)
            self._update_benchmark(
                original.benchmark_id,
                status="complete",
                progress=100,
                message="自动质检通过，等待用户完成整组盲听" if automatic_pass else "自动质检发现退化，请检查失败样本",
                automatic_pass=automatic_pass,
                samples=samples,
                error=None,
            )
        except Exception as exc:
            self.runtime_logger.exception("[RVC BENCHMARK] failed | benchmark=%s", original.benchmark_id)
            self._update_benchmark(
                original.benchmark_id,
                status="failed",
                message="RVC 基准生成失败",
                error=str(exc),
                samples=samples,
            )

    @staticmethod
    def _require_wav(audio: bytes, source: str) -> None:
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise RvcProblem(502, f"{source} 未返回有效 WAV 音频")

    def _resolve_binding_character_id(
        self,
        project_id: str,
        character_id: str | None,
        reference_id: str | None,
    ) -> str | None:
        if character_id and character_id != "narrator":
            return character_id
        if not reference_id:
            return character_id
        plan = self._read_json(self._project_root(project_id) / "reference_plan.json") or {}
        item = next((item for item in plan.get("items", []) if item.get("reference_id") == reference_id), None)
        if item is None:
            return character_id
        source_character_id = str(item.get("source_character_id") or character_id or "")
        if source_character_id == "narrator":
            gender = item.get("gender") if item.get("gender") in {"male", "female"} else "unknown"
            return f"narrator-{gender}"
        return source_character_id

    def _route_approval_current(
        self,
        project_id: str,
        character_id: str | None,
        model: RvcModelAsset,
        route: RvcRoute,
        canonical_path_value: object | None = None,
    ) -> bool:
        if model.status != "approved" or route not in model.approved_routes:
            return False
        if model.character_id and character_id and model.character_id != character_id:
            return False
        if not model.benchmark_ids:
            return True
        canonical_path = canonical_path_value if isinstance(canonical_path_value, Path) else None
        if canonical_path is None and character_id:
            material = next(
                (
                    item
                    for item in self._character_materials(project_id)
                    if item["character_id"] == character_id
                ),
                None,
            )
            candidate = material.get("canonical_path") if material else None
            canonical_path = candidate if isinstance(candidate, Path) else None
        if canonical_path is None:
            return False
        canonical_sha256 = self._sha256(canonical_path)
        profile = model.inference_profiles.get(
            route,
            default_quality_profile() if route == "quality" else default_fast_profile(),
        )
        profile_fingerprint = self._profile_fingerprint(profile)
        return any(
            report is not None
            and report.decision == "approved"
            and report.project_id == project_id
            and report.character_id == character_id
            and report.model_id == model.model_id
            and report.route == route
            and report.canonical_sha256 == canonical_sha256
            and report.profile_fingerprint == profile_fingerprint
            for report in (self._benchmarks.get(benchmark_id) for benchmark_id in model.benchmark_ids)
        )

    def workspace(self, project_id: str) -> RvcWorkspaceView:
        self._project_root(project_id)
        models = self._scan_models()
        model_by_id = {model.model_id: model for model in models}
        model_ids = set(model_by_id)
        settings = self._read_settings(project_id)
        setting_by_character = {item.character_id: item for item in settings.characters}
        with self._lock:
            project_jobs = [job.model_copy(deep=True) for job in self._jobs.values() if job.project_id == project_id]
        active_jobs = {
            job.character_id: job.job_id for job in project_jobs if job.status in {"queued", "running"}
        }
        characters: list[RvcCharacterView] = []
        for material in self._character_materials(project_id):
            selected = setting_by_character.get(material["character_id"])
            selected_model_id = selected.selected_model_id if selected and selected.selected_model_id in model_ids else None
            selected_model = model_by_id.get(selected_model_id) if selected_model_id else None
            quality_approved = bool(
                selected_model
                and self._route_approval_current(
                    project_id,
                    str(material["character_id"]),
                    selected_model,
                    "quality",
                    material.get("canonical_path"),
                )
            )
            fast_approved = bool(
                selected_model
                and self._route_approval_current(
                    project_id,
                    str(material["character_id"]),
                    selected_model,
                    "fast",
                    material.get("canonical_path"),
                )
            )
            characters.append(
                RvcCharacterView(
                    character_id=material["character_id"],
                    reference_id=material["reference_id"],
                    display_name=material["display_name"],
                    gender=material["gender"],
                    material_count=len(material["paths"]),
                    material_duration_seconds=round(material["duration"], 2),
                    training_ready=material["duration"] >= MINIMUM_TRAINING_SECONDS,
                    sample_audio_url=material["sample_url"],
                    train_enabled=selected.train_enabled if selected else False,
                    stability_enabled=(
                        selected.stability_enabled
                        if selected and selected_model_id and quality_approved
                        else False
                    ),
                    fast_route_enabled=(
                        selected.fast_route_enabled
                        if selected and selected_model_id and fast_approved
                        else False
                    ),
                    selected_model_id=selected_model_id,
                    selected_model_status=selected_model.status if selected_model else None,
                    quality_approved=quality_approved,
                    fast_approved=fast_approved,
                    active_job_id=active_jobs.get(material["character_id"]),
                )
            )
        runtime_available = bool(getattr(self.runner, "available", True))
        runtime_detail = (
            f"{getattr(self.runner, 'detail', '托管 RVC 训练运行器可用')} · "
            f"{getattr(self.inference_runner, 'detail', 'RVC 推理运行器可用')}"
        )
        jobs = sorted(project_jobs, key=lambda item: item.created_at, reverse=True)[:20]
        benchmarks = sorted(
            (
                report.model_copy(deep=True)
                for report in self._benchmarks.values()
                if report.project_id == project_id
            ),
            key=lambda item: item.created_at,
            reverse=True,
        )[:20]
        return RvcWorkspaceView(
            project_id=project_id,
            tool_available=self.rvc_root.is_dir(),
            training_runtime_available=runtime_available,
            runtime_detail=runtime_detail,
            settings=settings,
            characters=characters,
            models=models,
            training_sets=self._list_training_sets(project_id),
            benchmarks=benchmarks,
            jobs=jobs,
        )

    def current_approved_model(
        self,
        project_id: str,
        character_id: str,
        route: RvcRoute = "quality",
    ) -> RvcModelAsset | None:
        material = next(
            (item for item in self._character_materials(project_id) if item["character_id"] == character_id),
            None,
        )
        if material is None:
            return None
        candidates = sorted(self._scan_models(), key=lambda item: item.updated_at, reverse=True)
        return next(
            (
                model.model_copy(deep=True)
                for model in candidates
                if model.character_id == character_id
                and model.index_path is not None
                and self._route_approval_current(
                    project_id,
                    character_id,
                    model,
                    route,
                    material.get("canonical_path"),
                )
            ),
            None,
        )

    def update_settings(self, project_id: str, request: RvcSettingsUpdate) -> RvcWorkspaceView:
        available_characters = {item["character_id"] for item in self._character_materials(project_id)}
        models = {model.model_id: model for model in self._scan_models()}
        with self._lock:
            settings = self._read_settings(project_id)
            by_character = {item.character_id: item for item in settings.characters}
            if request.character_id:
                if request.character_id not in available_characters:
                    raise RvcProblem(404, "RVC 角色不存在或尚未生成参考计划")
                current = by_character.get(
                    request.character_id,
                    RvcCharacterSettings(character_id=request.character_id),
                )
                if "selected_model_id" in request.model_fields_set:
                    if request.selected_model_id is not None and request.selected_model_id not in models:
                        raise RvcProblem(404, "选择的 RVC 模型不存在")
                    if (
                        request.selected_model_id is not None
                        and models[request.selected_model_id].character_id
                        and models[request.selected_model_id].character_id != request.character_id
                    ):
                        raise RvcProblem(409, "该 RVC 模型属于其他角色，不能跨角色绑定")
                    selected_model_id = request.selected_model_id
                else:
                    selected_model_id = current.selected_model_id
                stability_enabled = (
                    request.stability_enabled
                    if request.stability_enabled is not None
                    else current.stability_enabled
                )
                fast_route_enabled = (
                    request.fast_character_enabled
                    if request.fast_character_enabled is not None
                    else current.fast_route_enabled
                )
                if stability_enabled and (
                    not selected_model_id
                    or models[selected_model_id].index_path is None
                    or not self._route_approval_current(
                        project_id,
                        request.character_id,
                        models[selected_model_id],
                        "quality",
                    )
                ):
                    raise RvcProblem(409, "质量稳定层只能启用已通过质量路线基准的 RVC 模型")
                if fast_route_enabled and (
                    not selected_model_id
                    or models[selected_model_id].index_path is None
                    or not self._route_approval_current(
                        project_id,
                        request.character_id,
                        models[selected_model_id],
                        "fast",
                    )
                ):
                    raise RvcProblem(409, "极速身份层只能启用已通过极速路线基准的 RVC 模型")
                by_character[request.character_id] = RvcCharacterSettings(
                    character_id=request.character_id,
                    train_enabled=request.train_enabled if request.train_enabled is not None else current.train_enabled,
                    stability_enabled=stability_enabled,
                    fast_route_enabled=fast_route_enabled,
                    selected_model_id=selected_model_id,
                )
            quality_enabled = (
                request.quality_stability_enabled
                if request.quality_stability_enabled is not None
                else settings.quality_stability_enabled
            )
            if quality_enabled and not any(
                item.stability_enabled
                and item.selected_model_id in models
                and models[item.selected_model_id].index_path is not None
                and self._route_approval_current(
                    project_id,
                    item.character_id,
                    models[item.selected_model_id],
                    "quality",
                )
                for item in by_character.values()
            ):
                raise RvcProblem(409, "至少为一个角色启用已通过质量基准的 RVC 模型")
            fast_enabled = (
                request.fast_route_enabled
                if request.fast_route_enabled is not None
                else settings.fast_route_enabled
            )
            if fast_enabled and not any(
                item.fast_route_enabled
                and item.selected_model_id in models
                and models[item.selected_model_id].index_path is not None
                and self._route_approval_current(
                    project_id,
                    item.character_id,
                    models[item.selected_model_id],
                    "fast",
                )
                for item in by_character.values()
            ):
                raise RvcProblem(409, "至少为一个角色启用已通过极速基准的 RVC 模型")
            updated = RvcProjectSettings(
                quality_stability_enabled=quality_enabled,
                fast_route_enabled=fast_enabled,
                training_options=request.training_options or settings.training_options,
                characters=sorted(by_character.values(), key=lambda item: item.character_id),
            )
            self._write_settings(project_id, updated)
        return self.workspace(project_id)

    def update_inference_profile(
        self,
        project_id: str,
        model_id: str,
        route: RvcRoute,
        request: RvcProfileUpdateRequest,
    ) -> RvcWorkspaceView:
        self._project_root(project_id)
        models = {model.model_id: model for model in self._scan_models()}
        model = models.get(model_id)
        if model is None or model.index_path is None:
            raise RvcProblem(404, "RVC 模型或索引不存在")
        settings = self._read_settings(project_id)
        bound_character_ids = {
            item.character_id for item in settings.characters if item.selected_model_id == model_id
        }
        available_character_ids = {
            str(item["character_id"]) for item in self._character_materials(project_id)
        }
        if not bound_character_ids and model.character_id not in available_character_ids:
            raise RvcProblem(409, "只能修改当前项目已绑定或所属角色的 RVC 模型参数")

        profiles = dict(model.inference_profiles)
        profiles[route] = request.profile.model_copy(deep=True)
        approved_routes = [item for item in model.approved_routes if item != route]
        updated_model = model.model_copy(
            update={
                "inference_profiles": profiles,
                "profile_fingerprints": {
                    key: self._profile_fingerprint(value) for key, value in profiles.items()
                },
                "approved_routes": approved_routes,
                "status": "approved" if approved_routes else "candidate",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._write_model_manifest(updated_model)

        characters: list[RvcCharacterSettings] = []
        for item in settings.characters:
            if item.selected_model_id != model_id:
                characters.append(item)
                continue
            changes = (
                {"stability_enabled": False}
                if route == "quality"
                else {"fast_route_enabled": False}
            )
            characters.append(item.model_copy(update=changes))
        quality_enabled = settings.quality_stability_enabled
        fast_enabled = settings.fast_route_enabled
        if route == "quality" and not any(item.stability_enabled for item in characters):
            quality_enabled = False
        if route == "fast" and not any(item.fast_route_enabled for item in characters):
            fast_enabled = False
        self._write_settings(
            project_id,
            settings.model_copy(
                update={
                    "characters": characters,
                    "quality_stability_enabled": quality_enabled,
                    "fast_route_enabled": fast_enabled,
                }
            ),
        )
        return self.workspace(project_id)

    def submit(self, project_id: str, request: RvcTrainingRequest) -> RvcTrainingJob:
        material = next(
            (item for item in self._character_materials(project_id) if item["character_id"] == request.character_id),
            None,
        )
        if material is None:
            raise RvcProblem(404, "RVC 角色不存在或尚未生成参考计划")
        canonical_path = material.get("canonical_path")
        canonical_url = material.get("canonical_url")
        if not isinstance(canonical_path, Path) or not isinstance(canonical_url, str):
            raise RvcProblem(409, "请先确认当前角色的标准参考音频，再构建 RVC 训练集")
        if not bool(getattr(self.runner, "available", True)):
            raise RvcProblem(409, str(getattr(self.runner, "detail", "RVC 训练运行时不可用")))
        settings = self._read_settings(project_id)
        options = request.options or settings.training_options
        now = datetime.now(timezone.utc)
        job_id = uuid.uuid4().hex[:12]
        experiment_name = f"zw_{self._training_slug(request.character_id)}_{job_id}"
        model_root = self.models_root / project_id / request.character_id
        minimum_duration = self._minimum_training_seconds(request.purpose)
        existing_revision = material.get("training_set_revision")
        if not isinstance(existing_revision, RvcTrainingSetRevision) or (
            existing_revision.purpose != request.purpose
            or existing_revision.total_duration_seconds < minimum_duration
        ):
            revision_id = f"rvcset-{uuid.uuid4().hex[:12]}"
            existing_revision = RvcTrainingSetRevision(
                revision_id=revision_id,
                project_id=project_id,
                character_id=request.character_id,
                reference_id=str(material["reference_id"]),
                canonical_audio_version_id=(
                    str(material["canonical_version_id"])
                    if material.get("canonical_version_id")
                    else None
                ),
                canonical_audio_url=canonical_url,
                canonical_sha256=self._sha256(canonical_path),
                purpose=request.purpose,
                status="building",
                minimum_duration_seconds=minimum_duration,
                created_at=now,
            )
            self._write_training_set(existing_revision)
            input_paths = (canonical_path,)
            initial_duration = self._wav_duration(canonical_path)
            initial_count = 1
        else:
            input_paths = tuple(
                path
                for clip in existing_revision.clips
                if clip.accepted
                for path in [self._resolve_workspace_path(clip.path)]
                if path is not None
            )
            initial_duration = existing_revision.total_duration_seconds
            initial_count = len(input_paths)
        record = RvcTrainingJob(
            job_id=job_id,
            project_id=project_id,
            character_id=request.character_id,
            display_name=material["display_name"],
            status="queued",
            progress=0,
            message="已进入 GPU 队列，将先构建受标准参考约束的训练集",
            stage="queued",
            total_epochs=options.epochs,
            options=options,
            material_count=initial_count,
            material_duration_seconds=round(initial_duration, 2),
            purpose=request.purpose,
            training_set_revision_id=existing_revision.revision_id,
            log_id=f"rvc/{job_id}.log",
            created_at=now,
            updated_at=now,
        )
        spec = RvcTrainingSpec(
            job_id=job_id,
            project_id=project_id,
            character_id=request.character_id,
            display_name=material["display_name"],
            experiment_name=experiment_name,
            workspace_root=self.workspace_root,
            rvc_root=self.rvc_root,
            input_audio_paths=input_paths,
            output_model_path=model_root / f"{experiment_name}.pth",
            output_index_path=model_root / f"{experiment_name}.index",
            options=options,
            purpose=request.purpose,
            training_set_revision_id=existing_revision.revision_id,
            log_id=f"rvc/{job_id}.log",
        )
        with self._lock:
            if any(
                job.project_id == project_id
                and job.character_id == request.character_id
                and job.status in {"queued", "running"}
                for job in self._jobs.values()
            ):
                raise RvcProblem(409, "该角色已有 RVC 训练任务正在进行")
            settings_by_character = {item.character_id: item for item in settings.characters}
            current_character_settings = settings_by_character.get(
                request.character_id,
                RvcCharacterSettings(character_id=request.character_id),
            )
            settings_by_character[request.character_id] = current_character_settings.model_copy(
                update={"train_enabled": True}
            )
            self._write_settings(
                project_id,
                settings.model_copy(
                    update={
                        "characters": sorted(
                            settings_by_character.values(),
                            key=lambda item: item.character_id,
                        )
                    }
                ),
            )
            self._jobs[job_id] = record
            self._persist_job(record)
            future = self._executor.submit(
                self._run,
                record,
                spec,
                str(material.get("voice_prompt") or "自然、清晰、稳定的角色声线"),
            )
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget_future(job_id))
        return record.model_copy(deep=True)

    def get_job(self, job_id: str) -> RvcTrainingJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RvcProblem(404, "RVC 训练任务不存在")
            return job.model_copy(deep=True)

    def cancel(self, job_id: str) -> RvcTrainingJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RvcProblem(404, "RVC 训练任务不存在")
            if job.status not in {"queued", "running"}:
                raise RvcProblem(409, "RVC 训练任务已经结束")
            self._cancelled.add(job_id)
            updated = job.model_copy(
                update={
                    "status": "cancelled",
                    "stage": "cancelled",
                    "message": "RVC 训练已取消",
                    "completed_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._jobs[job_id] = updated
            self._persist_job(updated)
        self.runner.cancel(job_id)
        return updated.model_copy(deep=True)

    def _run(self, record: RvcTrainingJob, spec: RvcTrainingSpec, voice_prompt: str) -> None:
        try:
            self._update_job(
                record.job_id,
                status="running",
                stage="preparing_material",
                progress=5,
                message="正在准备角色训练集",
                started_at=datetime.now(timezone.utc),
                total_epochs=spec.options.epochs,
            )
            with self._gpu_lock:
                spec = self._ensure_training_material(record, spec, voice_prompt)
                self._update_job(
                    record.job_id,
                    status="running",
                    stage="starting_training",
                    progress=30,
                    message="训练材料已就绪，正在启动 RVC",
                    total_epochs=spec.options.epochs,
                )

                def on_runner_progress(
                    runner_progress: int,
                    message: str,
                    stage: RvcTrainingStage | None = None,
                    current_epoch: int | None = None,
                    total_epochs: int | None = None,
                    last_log: str | None = None,
                ) -> None:
                    self._update_job(
                        record.job_id,
                        status="running",
                        progress=max(30, min(98, 30 + round(runner_progress * 0.68))),
                        message=message,
                        stage=stage or "training",
                        current_epoch=current_epoch,
                        total_epochs=total_epochs or spec.options.epochs,
                        last_log=last_log,
                    )

                artifact = self.runner.run(
                    spec,
                    on_runner_progress,
                    lambda: self._is_cancelled(record.job_id),
                )
            if self._is_cancelled(record.job_id):
                return
            model = self._register_trained_model(record, spec, artifact)
            self._bind_trained_model(record.project_id, record.character_id, model.model_id)
            self._update_job(
                record.job_id,
                status="complete",
                stage="complete",
                progress=100,
                message="RVC 候选模型已生成，完成路线基准后才能启用",
                current_epoch=spec.options.epochs,
                total_epochs=spec.options.epochs,
                model_id=model.model_id,
            )
        except Exception as exc:
            if self._is_cancelled(record.job_id):
                return
            revision = self._read_training_set(record.training_set_revision_id or "")
            if revision is not None and revision.status == "building":
                self._write_training_set(
                    revision.model_copy(
                        update={
                            "status": "failed",
                            "error": str(exc),
                            "completed_at": datetime.now(timezone.utc),
                        }
                    )
                )
            self._update_job(
                record.job_id,
                status="failed",
                stage="failed",
                message="RVC 训练失败",
                error=str(exc),
            )

    def _ensure_training_material(
        self,
        record: RvcTrainingJob,
        spec: RvcTrainingSpec,
        voice_prompt: str,
    ) -> RvcTrainingSpec:
        revision = self._read_training_set(spec.training_set_revision_id)
        if revision is None:
            raise RuntimeError("RVC 训练集版本不存在")
        accepted_paths = [
            path
            for clip in revision.clips
            if clip.accepted
            for path in [self._resolve_workspace_path(clip.path)]
            if path is not None
        ]
        if revision.status == "ready" and revision.total_duration_seconds >= revision.minimum_duration_seconds:
            return replace(spec, input_audio_paths=tuple(accepted_paths))
        if not spec.input_audio_paths:
            raise RuntimeError("RVC 训练集缺少标准参考音频")

        revision_root = self._training_set_root(revision)
        audio_root = revision_root / "audio"
        audio_root.mkdir(parents=True, exist_ok=True)
        clips = list(revision.clips)
        canonical_copy = audio_root / "000-canonical.wav"
        if not canonical_copy.is_file():
            shutil.copyfile(spec.input_audio_paths[0], canonical_copy)
        if not any(clip.source == "canonical" for clip in clips):
            clips.append(self._training_clip(canonical_copy, "canonical"))
        accepted_paths = [
            path
            for clip in clips
            if clip.accepted
            for path in [self._resolve_workspace_path(clip.path)]
            if path is not None
        ]
        duration = sum(self._wav_duration(path) for path in accepted_paths)
        revision = revision.model_copy(
            update={
                "clips": clips,
                "total_duration_seconds": round(duration, 3),
                "status": "building",
                "error": None,
            }
        )
        self._write_training_set(revision)
        if duration >= revision.minimum_duration_seconds:
            ready = revision.model_copy(
                update={"status": "ready", "completed_at": datetime.now(timezone.utc)}
            )
            self._write_training_set(ready)
            return replace(spec, input_audio_paths=tuple(accepted_paths))
        if self.preview_gateway is None:
            raise RuntimeError("训练材料不足，且受标准参考约束的素材生成器不可用")

        maximum_clips = len(RVC_MATERIAL_TEXTS) * 3
        generated_count = sum(1 for clip in clips if clip.source == "reference_conditioned")
        for generated_index in range(generated_count, maximum_clips):
            if self._is_cancelled(record.job_id):
                raise RuntimeError("RVC 训练已取消")
            text = RVC_MATERIAL_TEXTS[generated_index % len(RVC_MATERIAL_TEXTS)]
            self._update_job(
                record.job_id,
                status="running",
                stage="preparing_material",
                progress=min(28, 7 + round(21 * generated_index / max(1, maximum_clips))),
                message=(
                    f"正在用标准参考扩充训练素材 {generated_index + 1}/{maximum_clips} · "
                    f"{duration:.1f}/{revision.minimum_duration_seconds} 秒"
                ),
            )
            audio = self.preview_gateway.generate_quality(
                text,
                canonical_copy,
                "gpt_sovits_v2_pro_plus",
                None,
                QualityRenderOptions(),
            )
            if self._is_cancelled(record.job_id):
                raise RuntimeError("RVC 训练已取消")
            self._require_wav(audio, "标准参考约束素材生成器")
            output = audio_root / f"{generated_index + 1:03d}-conditioned.wav"
            temporary = output.with_suffix(".wav.tmp")
            temporary.write_bytes(audio)
            temporary.replace(output)
            clip = self._training_clip(output, "reference_conditioned", text)
            clips.append(clip)
            if clip.accepted:
                accepted_paths.append(output)
                duration += clip.duration_seconds
            revision = revision.model_copy(
                update={
                    "clips": clips,
                    "total_duration_seconds": round(duration, 3),
                }
            )
            self._write_training_set(revision)
            self._update_job(
                record.job_id,
                material_count=len(accepted_paths),
                material_duration_seconds=round(duration, 2),
                stage="preparing_material",
                message=f"训练素材已通过质检 {duration:.1f}/{revision.minimum_duration_seconds} 秒",
            )
            if duration >= revision.minimum_duration_seconds:
                ready = revision.model_copy(
                    update={"status": "ready", "completed_at": datetime.now(timezone.utc)}
                )
                self._write_training_set(ready)
                return replace(spec, input_audio_paths=tuple(accepted_paths))

        failed = revision.model_copy(
            update={
                "status": "failed",
                "error": (
                    f"受标准参考约束的有效素材不足 {revision.minimum_duration_seconds} 秒，"
                    f"当前 {duration:.1f} 秒"
                ),
                "completed_at": datetime.now(timezone.utc),
            }
        )
        self._write_training_set(failed)
        raise RuntimeError(failed.error)

    def _bind_trained_model(self, project_id: str, character_id: str, model_id: str) -> None:
        with self._lock:
            settings = self._read_settings(project_id)
            by_character = {item.character_id: item for item in settings.characters}
            current = by_character.get(character_id, RvcCharacterSettings(character_id=character_id))
            by_character[character_id] = current.model_copy(
                update={"train_enabled": True, "selected_model_id": model_id}
            )
            self._write_settings(
                project_id,
                settings.model_copy(update={"characters": sorted(by_character.values(), key=lambda item: item.character_id)}),
            )

    def _character_materials(self, project_id: str) -> list[dict[str, object]]:
        project_root = self._project_root(project_id)
        plan = self._read_json(project_root / "reference_plan.json")
        if not plan:
            return []
        materials: list[dict[str, object]] = []
        for item in plan.get("items", []):
            if not item.get("selected"):
                continue
            reference_id = str(item.get("reference_id", ""))
            audio_versions = item.get("audio_versions") if isinstance(item.get("audio_versions"), list) else []
            active_version_id = item.get("active_audio_version_id")
            active_version = next(
                (
                    version
                    for version in audio_versions
                    if isinstance(version, dict) and version.get("version_id") == active_version_id
                ),
                None,
            )
            accepted_active_version = (
                active_version
                if isinstance(active_version, dict)
                and active_version.get("decision", "accepted") == "accepted"
                else None
            )
            canonical_url = (
                accepted_active_version.get("audio_url")
                if accepted_active_version is not None
                and isinstance(accepted_active_version.get("audio_url"), str)
                else (
                    item.get("audio_url")
                    if not audio_versions and isinstance(item.get("audio_url"), str)
                    else None
                )
            )
            canonical_path = self._resolve_media(canonical_url) if canonical_url else None
            paths = [canonical_path] if canonical_path is not None else []
            duration = self._wav_duration(canonical_path) if canonical_path is not None else 0.0
            source_character_id = str(item.get("source_character_id") or reference_id)
            gender = item.get("gender") if item.get("gender") in {"male", "female"} else "unknown"
            character_id = f"narrator-{gender}" if source_character_id == "narrator" else source_character_id
            canonical_version_id = active_version_id if canonical_url else None
            latest_training_set = next(
                (
                    revision
                    for revision in self._list_training_sets(project_id)
                    if revision.character_id == character_id
                    and revision.status == "ready"
                    and revision.canonical_audio_version_id == canonical_version_id
                    and canonical_path is not None
                    and revision.canonical_sha256 == self._sha256(canonical_path)
                ),
                None,
            )
            if latest_training_set is not None:
                revision_paths = [
                    self._resolve_workspace_path(clip.path)
                    for clip in latest_training_set.clips
                    if clip.accepted
                ]
                paths = [path for path in revision_paths if path is not None]
                duration = latest_training_set.total_duration_seconds
            materials.append(
                {
                    "character_id": character_id,
                    "reference_id": reference_id,
                    "display_name": str(item.get("display_name", "未命名角色")),
                    "gender": gender,
                    "paths": paths,
                    "duration": duration,
                    "sample_url": canonical_url,
                    "canonical_url": canonical_url,
                    "canonical_path": canonical_path,
                    "canonical_version_id": canonical_version_id,
                    "training_set_revision": latest_training_set,
                    "voice_prompt": str(item.get("voice_prompt") or ""),
                }
            )
        return materials

    @staticmethod
    def _minimum_training_seconds(purpose: RvcTrainingPurpose) -> int:
        return (
            FAST_IDENTITY_MINIMUM_TRAINING_SECONDS
            if purpose in {"fast_identity", "both"}
            else MINIMUM_TRAINING_SECONDS
        )

    def _training_set_root(self, revision: RvcTrainingSetRevision) -> Path:
        return (
            self.training_sets_root
            / revision.project_id
            / self._training_slug(revision.character_id)
            / revision.revision_id
        )

    def _training_set_manifest_path(self, revision: RvcTrainingSetRevision) -> Path:
        return self._training_set_root(revision) / "manifest.json"

    def _write_training_set(self, revision: RvcTrainingSetRevision) -> None:
        path = self._training_set_manifest_path(revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(revision.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _read_training_set(self, revision_id: str) -> RvcTrainingSetRevision | None:
        if not revision_id:
            return None
        for path in self.training_sets_root.rglob("manifest.json"):
            try:
                revision = RvcTrainingSetRevision.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if revision.revision_id == revision_id:
                return revision
        return None

    def _list_training_sets(self, project_id: str) -> list[RvcTrainingSetRevision]:
        project_root = self.training_sets_root / project_id
        if not project_root.is_dir():
            return []
        revisions: list[RvcTrainingSetRevision] = []
        for path in project_root.rglob("manifest.json"):
            try:
                revisions.append(RvcTrainingSetRevision.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(revisions, key=lambda item: item.created_at, reverse=True)

    def _training_clip(
        self,
        path: Path,
        source: Literal["canonical", "reference_conditioned"],
        text: str | None = None,
    ) -> RvcTrainingSetClip:
        duration = self._wav_duration(path)
        peak_dbfs: float | None = None
        clipping_ratio = 0.0
        silence_ratio = 0.0
        rejection_reason: str | None = None
        try:
            with wave.open(str(path), "rb") as audio:
                frames = audio.readframes(audio.getnframes())
                if audio.getsampwidth() == 2 and frames:
                    samples = array("h")
                    samples.frombytes(frames)
                    magnitudes = [abs(sample) for sample in samples]
                    peak = max(magnitudes, default=0)
                    peak_dbfs = round(20 * math.log10(max(1, peak) / 32767), 3)
                    clipping_ratio = round(
                        sum(1 for magnitude in magnitudes if magnitude >= 32_440) / len(magnitudes),
                        6,
                    )
                    silence_ratio = round(
                        sum(1 for magnitude in magnitudes if magnitude <= 328) / len(magnitudes),
                        6,
                    )
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            rejection_reason = "WAV 无法读取"
        if duration < 1:
            rejection_reason = "音频短于 1 秒"
        elif clipping_ratio > 0.005:
            rejection_reason = "削波比例超过 0.5%"
        elif silence_ratio > 0.85:
            rejection_reason = "静音比例超过 85%"
        elif peak_dbfs is not None and peak_dbfs < -45:
            rejection_reason = "有效信号电平低于 -45 dBFS"
        return RvcTrainingSetClip(
            clip_id=f"clip-{uuid.uuid4().hex[:12]}",
            source=source,
            text=text,
            path=self._display_path(path),
            sha256=self._sha256(path),
            duration_seconds=round(duration, 3),
            peak_dbfs=peak_dbfs,
            clipping_ratio=clipping_ratio,
            silence_ratio=silence_ratio,
            accepted=rejection_reason is None,
            rejection_reason=rejection_reason,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _profile_fingerprint(profile: RvcInferenceProfile) -> str:
        return hashlib.sha256(profile.model_dump_json().encode("utf-8")).hexdigest()[:16]

    def _scan_models(self) -> list[RvcModelAsset]:
        weight_roots = [
            (self.rvc_root / "assets" / "weights", "existing"),
            (self.workspace_root / "assets" / "rvc_models", "existing"),
            (self.models_root, "trained"),
        ]
        index_roots = [
            self.rvc_root / "assets" / "indices",
            self.workspace_root / "assets" / "rvc_models",
            self.models_root,
        ]
        indices = [path for root in index_roots if root.is_dir() for path in root.rglob("*.index")]
        assets: list[RvcModelAsset] = []
        seen: set[Path] = set()
        for path in self.model_manifests_root.glob("*.json"):
            try:
                asset = RvcModelAsset.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            asset = asset.model_copy(
                update={
                    "profile_fingerprints": {
                        route: self._profile_fingerprint(profile)
                        for route, profile in asset.inference_profiles.items()
                    }
                }
            )
            weight = self._resolve_workspace_path(asset.weight_path)
            index = self._resolve_workspace_path(asset.index_path) if asset.index_path else None
            if weight is None:
                asset = asset.model_copy(update={"status": "stale"})
            elif asset.index_path and index is None:
                asset = asset.model_copy(update={"status": "stale"})
            else:
                seen.add(weight.resolve())
            assets.append(
                asset.model_copy(update={"manifest_path": self._display_path(path)})
            )
        for root, source in weight_roots:
            if not root.is_dir():
                continue
            for weight in root.rglob("*.pth"):
                resolved = weight.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                index = self._best_index(weight, indices)
                stat = weight.stat()
                assets.append(
                    RvcModelAsset(
                        model_id=self._model_id(weight),
                        label=weight.stem,
                        weight_path=self._display_path(weight),
                        index_path=self._display_path(index) if index else None,
                        source=source,
                        status="unverified",
                        approved_routes=[],
                        profile_fingerprints={
                            "quality": self._profile_fingerprint(default_quality_profile()),
                            "fast": self._profile_fingerprint(default_fast_profile()),
                        },
                        manifest_path=None,
                        size_mb=round(stat.st_size / 1024 / 1024, 1),
                        updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                    )
                )
        return sorted(assets, key=lambda item: item.updated_at, reverse=True)

    def _write_model_manifest(self, asset: RvcModelAsset) -> RvcModelAsset:
        path = self.model_manifests_root / f"{asset.model_id}.json"
        persisted = asset.model_copy(update={"manifest_path": self._display_path(path)})
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(persisted.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return persisted

    def _register_trained_model(
        self,
        record: RvcTrainingJob,
        spec: RvcTrainingSpec,
        artifact: RvcTrainingArtifact,
    ) -> RvcModelAsset:
        stat = artifact.model_path.stat()
        model_id = self._model_id(artifact.model_path)
        asset = RvcModelAsset(
            model_id=model_id,
            label=record.display_name,
            weight_path=self._display_path(artifact.model_path),
            index_path=self._display_path(artifact.index_path),
            source="trained",
            character_id=record.character_id,
            training_set_revision_id=spec.training_set_revision_id,
            status="candidate",
            approved_routes=[],
            inference_profiles={
                "quality": default_quality_profile(),
                "fast": default_fast_profile(),
            },
            profile_fingerprints={
                "quality": self._profile_fingerprint(default_quality_profile()),
                "fast": self._profile_fingerprint(default_fast_profile()),
            },
            size_mb=round(stat.st_size / 1024 / 1024, 1),
            updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        )
        return self._write_model_manifest(asset)

    @staticmethod
    def _best_index(weight: Path, indices: list[Path]) -> Path | None:
        candidates = [weight.stem, re.sub(r"_e\d+_s\d+$", "", weight.stem, flags=re.IGNORECASE)]
        matches = [
            index
            for index in indices
            if any(candidate.casefold() in index.stem.casefold() for candidate in candidates if candidate)
        ]
        return min(matches, key=lambda path: (len(path.stem), str(path))) if matches else None

    def _resolve_media(self, media_url: str) -> Path | None:
        parsed = urlsplit(media_url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
        decoded = unquote(parsed.path)
        roots = {
            "/media/outputs/audio/": self.workspace_root / "outputs" / "audio",
            "/media/outputs/projects/": self.workspace_root / "outputs" / "projects",
            "/media/voice-samples/": self.workspace_root / "assets" / "voice_samples",
        }
        for prefix, root in roots.items():
            if not decoded.startswith(prefix):
                continue
            relative = PurePosixPath(decoded[len(prefix) :])
            if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                return None
            candidate = root.joinpath(*relative.parts).resolve()
            if candidate.is_relative_to(root.resolve()) and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as source:
                return source.getnframes() / source.getframerate()
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            return 0.0

    def _project_root(self, project_id: str) -> Path:
        if not project_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", project_id):
            raise RvcProblem(400, "项目 ID 无效")
        root = (self.workspace_root / "outputs" / "projects" / project_id).resolve()
        if not root.is_relative_to((self.workspace_root / "outputs" / "projects").resolve()):
            raise RvcProblem(400, "项目路径无效")
        return root

    def _settings_path(self, project_id: str) -> Path:
        return self._project_root(project_id) / "rvc_settings.json"

    def _read_settings(self, project_id: str) -> RvcProjectSettings:
        path = self._settings_path(project_id)
        if not path.is_file():
            return RvcProjectSettings()
        try:
            settings = RvcProjectSettings.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RvcProjectSettings()
        if (
            settings.training_options.epochs == 200
            and settings.training_options.save_every_epochs == 25
        ):
            settings = settings.model_copy(
                update={
                    "training_options": settings.training_options.model_copy(
                        update={"epochs": 20, "save_every_epochs": 5}
                    )
                }
            )
            self._write_settings(project_id, settings)
        return settings

    def _write_settings(self, project_id: str, settings: RvcProjectSettings) -> None:
        path = self._settings_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _load_jobs(self) -> None:
        for path in self.jobs_root.glob("*.json"):
            try:
                job = RvcTrainingJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.status in {"queued", "running"}:
                completed_at = datetime.now(timezone.utc)
                elapsed_seconds = 0.0
                if job.started_at is not None:
                    elapsed_seconds = max(0.0, (completed_at - job.started_at).total_seconds())
                job = job.model_copy(
                    update={
                        "status": "failed",
                        "stage": "failed",
                        "message": "服务重启前 RVC 训练未完成",
                        "error": "训练进程已中断，请重新提交",
                        "completed_at": completed_at,
                        "elapsed_seconds": round(elapsed_seconds, 1),
                        "updated_at": completed_at,
                    }
                )
                self._persist_job(job)
            self._jobs[job.job_id] = job

    def _load_benchmarks(self) -> None:
        for path in self.benchmarks_root.glob("*.json"):
            try:
                report = RvcBenchmarkReport.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if report.status in {"queued", "running"}:
                report = report.model_copy(
                    update={
                        "status": "failed",
                        "message": "服务重启前 RVC 基准未完成",
                        "error": "基准进程已中断，请重新提交",
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self._persist_benchmark(report)
            self._benchmarks[report.benchmark_id] = report

    def _update_benchmark(self, benchmark_id: str, **changes: object) -> RvcBenchmarkReport:
        with self._lock:
            current = self._benchmarks[benchmark_id]
            updated = current.model_copy(
                update={**changes, "updated_at": datetime.now(timezone.utc)}
            )
            self._benchmarks[benchmark_id] = updated
            self._persist_benchmark(updated)
            return updated.model_copy(deep=True)

    def _persist_benchmark(self, report: RvcBenchmarkReport) -> None:
        path = self.benchmarks_root / f"{report.benchmark_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _update_job(self, job_id: str, **changes: object) -> RvcTrainingJob:
        with self._lock:
            current = self._jobs[job_id]
            if current.status == "cancelled" and changes.get("status") != "cancelled":
                return current.model_copy(deep=True)
            now = datetime.now(timezone.utc)
            next_changes = dict(changes)
            next_status = str(next_changes.get("status", current.status))
            started_at = next_changes.get("started_at", current.started_at)
            if next_status == "running" and started_at is None:
                started_at = now
                next_changes["started_at"] = started_at
            if started_at is not None:
                completed_at = next_changes.get("completed_at", current.completed_at)
                if next_status in {"complete", "failed", "cancelled"} and completed_at is None:
                    completed_at = now
                    next_changes["completed_at"] = completed_at
                end_at = completed_at if completed_at is not None else now
                try:
                    next_changes["elapsed_seconds"] = round(
                        max(0.0, (end_at - started_at).total_seconds()),
                        1,
                    )
                except (AttributeError, TypeError):
                    pass
            updated = current.model_copy(
                update={**next_changes, "updated_at": now}
            )
            self._jobs[job_id] = updated
            self._persist_job(updated)
            return updated.model_copy(deep=True)

    def _persist_job(self, job: RvcTrainingJob) -> None:
        (self.jobs_root / f"{job.job_id}.json").write_text(
            json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled or self._jobs[job_id].status == "cancelled"

    def _forget_future(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(path.resolve())

    def _resolve_workspace_path(self, path_value: str) -> Path | None:
        path = Path(path_value)
        candidate = path if path.is_absolute() else self.workspace_root / path
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
        return None

    def _model_id(self, path: Path) -> str:
        digest = hashlib.sha1(self._display_path(path).encode("utf-8")).hexdigest()[:14]
        return f"rvc-{digest}"

    @staticmethod
    def _slug(value: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value, flags=re.UNICODE).strip("_")
        return cleaned[:48] or "character"

    @staticmethod
    def _training_slug(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_")
        return cleaned[:48] or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _read_json(path: Path) -> dict[str, object] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError):
            return None


def create_rvc_router(service: RvcService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["rvc"])

    @router.get("/projects/{project_id}/rvc/workspace", response_model=RvcWorkspaceView)
    def get_workspace(project_id: str) -> RvcWorkspaceView:
        try:
            return service.workspace(project_id)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.patch("/projects/{project_id}/rvc/settings", response_model=RvcWorkspaceView)
    def update_settings(project_id: str, request: RvcSettingsUpdate) -> RvcWorkspaceView:
        try:
            return service.update_settings(project_id, request)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.patch(
        "/projects/{project_id}/rvc/models/{model_id}/profiles/{route}",
        response_model=RvcWorkspaceView,
    )
    def update_inference_profile(
        project_id: str,
        model_id: str,
        route: RvcRoute,
        request: RvcProfileUpdateRequest,
    ) -> RvcWorkspaceView:
        try:
            return service.update_inference_profile(project_id, model_id, route, request)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.post(
        "/projects/{project_id}/rvc/training",
        response_model=RvcTrainingJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_training(project_id: str, request: RvcTrainingRequest) -> RvcTrainingJob:
        try:
            return service.submit(project_id, request)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.post("/projects/{project_id}/rvc/preview", response_model=RvcPreviewResult)
    def create_preview(project_id: str, request: RvcPreviewRequest) -> RvcPreviewResult:
        try:
            return service.create_preview(project_id, request)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.post(
        "/projects/{project_id}/rvc/benchmarks",
        response_model=RvcBenchmarkReport,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_benchmark(project_id: str, request: RvcBenchmarkRequest) -> RvcBenchmarkReport:
        try:
            return service.submit_benchmark(project_id, request)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.get("/rvc/benchmarks/{benchmark_id}", response_model=RvcBenchmarkReport)
    def get_benchmark(benchmark_id: str) -> RvcBenchmarkReport:
        try:
            return service.get_benchmark(benchmark_id)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.post("/rvc/benchmarks/{benchmark_id}/review", response_model=RvcWorkspaceView)
    def review_benchmark(
        benchmark_id: str,
        request: RvcBenchmarkReviewRequest,
    ) -> RvcWorkspaceView:
        try:
            return service.review_benchmark(benchmark_id, request)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.get("/rvc/jobs/{job_id}", response_model=RvcTrainingJob)
    def get_training_job(job_id: str) -> RvcTrainingJob:
        try:
            return service.get_job(job_id)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    @router.post("/rvc/jobs/{job_id}/cancel", response_model=RvcTrainingJob)
    def cancel_training_job(job_id: str) -> RvcTrainingJob:
        try:
            return service.cancel(job_id)
        except RvcProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    return router
