from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .domain import (
    CharacterEvidence,
    CharacterTier,
    CharacterVoice,
    CharacterVoiceBible,
    DirectorDocument,
    DirectorSegment,
    PerformanceDirection,
)


PreparationStatus = Literal["imported", "analyzed", "characters_ready", "director_ready"]
CandidateDecision = Literal["pending", "accepted", "rejected"]
PreparationAction = Literal["analyze", "extract_characters", "generate_director"]
ReferenceSelectionMode = Literal["automatic", "optional", "narrator_default"]
ReferenceGenerationStatus = Literal["not_generated", "queued", "running", "generated", "failed"]

REFERENCE_GENERATION_THRESHOLD = 0.75
REFERENCE_TEXT = "雨后的长街渐渐安静下来，我望着远处的灯火，平稳地说出今天的决定。"
FEMALE_CLUES = ("她", "女子", "少女", "小姐", "母亲", "姐姐", "妹妹", "妻子", "薰", "熏", "嫣", "妃", "仙")
MALE_CLUES = ("他", "男子", "少年", "先生", "父亲", "哥哥", "老者", "老")

CHAPTER_PATTERN = re.compile(r"^\s*第[零〇一二两三四五六七八九十百千万\d]+[章节回卷].*$", re.MULTILINE)
SPEECH_VERB = r"(?:说道|问道|答道|喝道|喊道|叫道|笑道|冷声道|沉声道|低声道|高声道|道|说|问|答)"
ATTRIBUTION_PATTERN = re.compile(
    rf'(?:^|[\n。！？!?；;，,”"』」])(?P<clause>[\u4e00-\u9fff]{{2,14}}?)(?P<verb>{SPEECH_VERB})(?P<introduces>[：:]?[“"『「]?)',
    re.MULTILINE,
)
ROLE_TERMS = (
    "少年",
    "少女",
    "老人",
    "老者",
    "男子",
    "女子",
    "众人",
    "弟子",
    "族人",
    "宗主",
    "族长",
    "长老",
    "导师",
    "老师",
    "先生",
    "小姐",
    "少爷",
    "父亲",
    "母亲",
    "爷爷",
    "哥哥",
    "姐姐",
    "大人",
    "前辈",
    "掌柜",
)
LEADING_PHRASES = ("这时", "只见", "忽然", "突然", "此时", "随后")
NON_NAME_PREFIXES = (
    "谁知",
    "想要",
    "前途",
    "怎么",
    "什么",
    "这个",
    "那个",
    "如果",
    "但是",
    "不过",
    "因此",
    "于是",
    "然后",
    "因为",
    "所以",
    "只是",
    "自己",
    "没有",
    "可以",
    "已经",
    "还是",
    "看来",
    "原来",
    "难怪",
    "虽然",
    "当然",
    "现在",
    "略微",
    "笑着",
    "闻言",
    "任何",
    "连忙",
    "平静",
    "仰头",
    "方才",
    "应该",
)
NON_NAME_ENDINGS = tuple("的地得着了是在有没不也都可会能要知想让将而与和却再才")
COMMON_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯卢莫房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢滑裴陆荣翁荀羊惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全班仰秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙叶司郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀浦尚农温庄晏柴瞿阎连茹习艾鱼容向古易廖步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁敖融冷訾辛阚毋乜鞠丰关蒯相查后荆红游竺权逯盖益桓公纳"
)
COMPOUND_SURNAMES = ("欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "纳兰")
NAME_SUFFIXES = ("儿", "老", "翁", "仙", "妃")
ALIAS_TRANSLATION = str.maketrans({"薰": "熏"})
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class PreparationProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SourceSummary(BaseModel):
    project_id: str
    file_name: str
    display_name: str
    size_bytes: int
    encoding: Literal["utf-8", "gb18030"]
    status: PreparationStatus


class AnalysisStructure(BaseModel):
    chapter_count: int
    character_count: int
    nonempty_line_count: int
    estimated_segment_count: int
    dialogue_count: int


class CharacterCandidate(BaseModel):
    candidate_id: str
    display_name: str
    decision: CandidateDecision
    confidence: float = Field(ge=0, le=1)
    mention_count: int
    dialogue_count: int
    evidence: list[str] = Field(default_factory=list)
    reason: str


class AnalysisAudit(BaseModel):
    schema_version: int = 1
    project_id: str
    source_file: str
    engine: Literal["rule_based_preview"] = "rule_based_preview"
    structure: AnalysisStructure
    candidates: list[CharacterCandidate]
    warnings: list[str] = Field(default_factory=list)


class ReferencePlanItem(BaseModel):
    reference_id: str
    source_character_id: str
    display_name: str
    gender: Literal["male", "female", "unknown"]
    importance: float = Field(ge=0, le=1)
    selection_mode: ReferenceSelectionMode
    selected: bool
    locked: bool
    reference_text: str
    voice_prompt: str
    reuse_reference_id: str | None = None
    job_id: str | None = None
    audio_url: str | None = None
    status: ReferenceGenerationStatus = "not_generated"
    error: str | None = None


class ReferencePlan(BaseModel):
    schema_version: int = 1
    project_id: str
    generation_backend: Literal["voxcpm2"] = "voxcpm2"
    automatic_threshold: float = REFERENCE_GENERATION_THRESHOLD
    items: list[ReferencePlanItem]


class PreparationPreview(BaseModel):
    project_id: str
    status: PreparationStatus
    source: SourceSummary
    analysis_audit: AnalysisAudit | None = None
    character_voice_bible: CharacterVoiceBible | None = None
    reference_plan: ReferencePlan | None = None
    director_doc: DirectorDocument | None = None


class PreparationActionRequest(BaseModel):
    action: PreparationAction


class ReferenceSelectionRequest(BaseModel):
    selected: bool


class PreparationService:
    """Owns the source-to-director preparation workflow and its persisted artifacts."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.source_root = self.workspace_root / "input"
        self.project_root = self.workspace_root / "outputs" / "projects"
        self._reference_lock = threading.RLock()
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.project_root.mkdir(parents=True, exist_ok=True)

    def list_sources(self) -> list[SourceSummary]:
        sources: list[SourceSummary] = []
        for path in sorted(self.source_root.glob("*.txt"), key=lambda item: item.name.casefold()):
            try:
                _, encoding = self._read_text(path)
            except UnicodeDecodeError:
                continue
            sources.append(self._source_summary(path, encoding))
        return sources

    def import_source(self, file_name: str, content: bytes) -> SourceSummary:
        if not file_name or file_name != Path(file_name).name or any(mark in file_name for mark in ("/", "\\", ":", "\0")):
            raise PreparationProblem(400, "文件名不安全，请选择本地 TXT 文件")
        if Path(file_name).suffix.casefold() != ".txt":
            raise PreparationProblem(415, "仅支持 TXT 小说文件")
        if not content:
            raise PreparationProblem(400, "TXT 文件为空")
        if len(content) > MAX_UPLOAD_BYTES:
            raise PreparationProblem(413, "TXT 文件超过 32 MB 限制")
        _, encoding = self._decode(content)
        target = self.source_root / file_name
        if target.exists():
            raise PreparationProblem(409, "同名 TXT 已存在，请先在列表中选择")
        target.write_bytes(content)
        return self._source_summary(target, encoding)

    def preview(self, project_id: str) -> PreparationPreview:
        source_path = self._find_source(project_id)
        _, encoding = self._read_text(source_path)
        audit = self._read_model(project_id, "analysis_audit.json", AnalysisAudit)
        bible = self._read_model(project_id, "character_voice_bible.json", CharacterVoiceBible)
        reference_plan = self._read_model(project_id, "reference_plan.json", ReferencePlan)
        if bible is not None and reference_plan is None:
            with self._reference_lock:
                reference_plan = self._read_model(project_id, "reference_plan.json", ReferencePlan)
                if reference_plan is None:
                    reference_plan = self._build_reference_plan(project_id, bible)
                    self._write_model(project_id, "reference_plan.json", reference_plan)
        director = self._read_model(project_id, "director_doc.json", DirectorDocument)
        return PreparationPreview(
            project_id=project_id,
            status=self._status(project_id),
            source=self._source_summary(source_path, encoding),
            analysis_audit=audit,
            character_voice_bible=bible,
            reference_plan=reference_plan,
            director_doc=director,
        )

    def run(self, project_id: str, action: PreparationAction) -> PreparationPreview:
        if action == "analyze":
            self._analyze(project_id)
        elif action == "extract_characters":
            self._extract_characters(project_id)
        else:
            self._generate_director(project_id)
        return self.preview(project_id)

    def _analyze(self, project_id: str) -> None:
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        segments = self._split_sentences(text)
        candidates = self._scan_candidates(text)
        warnings: list[str] = []
        if not CHAPTER_PATTERN.search(text):
            warnings.append("未识别到标准章节标题，导演文件将使用默认章节")
        if not any(candidate.decision == "pending" for candidate in candidates):
            warnings.append("未识别到具名说话人，请在角色审核阶段人工补充")
        audit = AnalysisAudit(
            project_id=project_id,
            source_file=source_path.name,
            structure=AnalysisStructure(
                chapter_count=len(CHAPTER_PATTERN.findall(text)),
                character_count=len(text),
                nonempty_line_count=sum(bool(line.strip()) for line in text.splitlines()),
                estimated_segment_count=len(segments),
                dialogue_count=len(re.findall(r"[“\"『「].+?[”\"』」]", text, re.DOTALL)),
            ),
            candidates=candidates,
            warnings=warnings,
        )
        self._write_model(project_id, "analysis_audit.json", audit)
        self._remove_artifact(project_id, "character_voice_bible.json")
        self._remove_artifact(project_id, "reference_plan.json")
        self._remove_artifact(project_id, "director_doc.json")

    def _extract_characters(self, project_id: str) -> None:
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        audit = self._require_model(project_id, "analysis_audit.json", AnalysisAudit, "请先分析文档")
        accepted = [candidate for candidate in audit.candidates if candidate.decision != "rejected"]
        for candidate in accepted:
            candidate.decision = "accepted"
        self._write_model(project_id, "analysis_audit.json", audit)

        canonical_candidates: list[CharacterCandidate] = []
        aliases: dict[str, list[str]] = {}
        for candidate in sorted(accepted, key=lambda item: -len(item.display_name)):
            canonical = next(
                (
                    existing
                    for existing in canonical_candidates
                    if self._alias_key(existing.display_name).endswith(self._alias_key(candidate.display_name))
                ),
                None,
            )
            if canonical is not None:
                aliases.setdefault(canonical.candidate_id, []).append(candidate.display_name)
            else:
                canonical_candidates.append(candidate)
        canonical_candidates.sort(key=lambda item: audit.candidates.index(item))

        peak_mentions = max((candidate.mention_count for candidate in canonical_candidates), default=1)
        characters = [
            CharacterVoice(
                character_id="narrator",
                display_name="旁白",
                confidence=1,
                importance=1,
                tier=CharacterTier.core,
                personality_tags=["叙事"],
                timbre_tags=["清晰", "稳定"],
                voice_prompt="成熟、清晰、稳定的叙述声线",
            )
        ]
        for candidate in canonical_candidates:
            importance = min(0.95, 0.25 + 0.7 * candidate.mention_count / peak_mentions)
            tier = CharacterTier.core if importance >= 0.75 else CharacterTier.supporting
            gender = self._infer_gender(candidate.display_name, candidate.evidence)
            characters.append(
                CharacterVoice(
                    character_id=self._stable_id("character", candidate.display_name),
                    display_name=candidate.display_name,
                    aliases=aliases.get(candidate.candidate_id, []),
                    confidence=candidate.confidence,
                    importance=round(importance, 3),
                    tier=tier,
                    gender=gender,
                    voice_prompt=self._voice_prompt(gender),
                    evidence=[
                        CharacterEvidence(
                            chapter_id="chapter-unknown",
                            segment_id=f"evidence-{index + 1:03d}",
                            text=evidence,
                            evidence_type="dialogue",
                        )
                        for index, evidence in enumerate(candidate.evidence[:5])
                    ],
                )
            )
        bible = CharacterVoiceBible(
            project_id=project_id,
            source_text=f"input/{source_path.name}",
            characters=characters,
        )
        self._write_model(project_id, "character_voice_bible.json", bible)
        self._write_model(project_id, "reference_plan.json", self._build_reference_plan(project_id, bible))
        self._remove_artifact(project_id, "director_doc.json")

    def update_reference_selection(
        self,
        project_id: str,
        reference_id: str,
        selected: bool,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            if item.locked and item.selected != selected:
                raise PreparationProblem(409, "自动生成项不能取消选择")
            item.selected = selected
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def record_reference_job(
        self,
        project_id: str,
        reference_id: str,
        job_id: str,
        status: Literal["queued", "running", "complete", "failed"],
        audio_url: str | None,
        error: str | None,
    ) -> None:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "参考计划不存在",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            if status == "queued" and not item.selected:
                raise PreparationProblem(409, "请先将该角色加入参考生成")
            item.job_id = job_id
            item.status = "generated" if status == "complete" else status
            item.audio_url = audio_url or item.audio_url
            item.error = error
            self._write_model(project_id, "reference_plan.json", plan)

    def _build_reference_plan(self, project_id: str, bible: CharacterVoiceBible) -> ReferencePlan:
        male_reference_id = self._stable_id("reference", f"{project_id}:narrator:male")
        female_reference_id = self._stable_id("reference", f"{project_id}:narrator:female")
        items = [
            ReferencePlanItem(
                reference_id=male_reference_id,
                source_character_id="narrator",
                display_name="男旁白",
                gender="male",
                importance=1,
                selection_mode="narrator_default",
                selected=True,
                locked=True,
                reference_text=REFERENCE_TEXT,
                voice_prompt="成熟、清晰、稳定的男性叙述声线",
            ),
            ReferencePlanItem(
                reference_id=female_reference_id,
                source_character_id="narrator",
                display_name="女旁白",
                gender="female",
                importance=1,
                selection_mode="narrator_default",
                selected=True,
                locked=True,
                reference_text=REFERENCE_TEXT,
                voice_prompt="成熟、清晰、稳定的女性叙述声线",
            ),
        ]
        for character in bible.characters:
            if character.character_id == "narrator":
                continue
            automatic = character.importance >= REFERENCE_GENERATION_THRESHOLD
            gender = character.gender if character.gender in {"male", "female"} else "unknown"
            items.append(
                ReferencePlanItem(
                    reference_id=self._stable_id("reference", f"{project_id}:{character.character_id}:neutral"),
                    source_character_id=character.character_id,
                    display_name=character.display_name,
                    gender=gender,
                    importance=character.importance,
                    selection_mode="automatic" if automatic else "optional",
                    selected=automatic,
                    locked=automatic,
                    reference_text=REFERENCE_TEXT,
                    voice_prompt=character.voice_prompt,
                    reuse_reference_id=female_reference_id if gender == "female" else male_reference_id,
                )
            )
        return ReferencePlan(project_id=project_id, items=items)

    @staticmethod
    def _infer_gender(display_name: str, evidence: list[str]) -> Literal["male", "female", "unknown"]:
        context = " ".join([display_name, *evidence])
        female_score = sum(context.count(clue) for clue in FEMALE_CLUES)
        male_score = sum(context.count(clue) for clue in MALE_CLUES)
        if female_score > male_score:
            return "female"
        if male_score > female_score:
            return "male"
        return "unknown"

    @staticmethod
    def _voice_prompt(gender: Literal["male", "female", "unknown"]) -> str:
        if gender == "female":
            return "自然、清晰、有辨识度的女性角色声线，保持中性情绪"
        if gender == "male":
            return "自然、清晰、有辨识度的男性角色声线，保持中性情绪"
        return "自然、清晰、有辨识度的角色声线，保持中性情绪"

    def _generate_director(self, project_id: str) -> None:
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        bible = self._require_model(
            project_id,
            "character_voice_bible.json",
            CharacterVoiceBible,
            "请先提取并审核角色",
        )
        character_ids = {
            name: character.character_id
            for character in bible.characters
            for name in [character.display_name, *character.aliases]
        }
        segments: list[DirectorSegment] = []
        chapter_number = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if CHAPTER_PATTERN.fullmatch(stripped):
                chapter_number += 1
                continue
            for sentence in self._split_sentences(stripped):
                speaker = self._speaker_for_sentence(sentence, character_ids)
                segments.append(
                    DirectorSegment(
                        segment_id=f"seg-{len(segments) + 1:06d}",
                        chapter_id=f"chapter-{max(chapter_number, 1):04d}",
                        character_id=character_ids.get(speaker, "narrator"),
                        text=sentence,
                        segment_type="dialogue" if speaker else "narration",
                        direction=PerformanceDirection(
                            emotion="natural" if speaker else "neutral",
                            pause_after_ms=220 if speaker else 180,
                        ),
                    )
                )
        director = DirectorDocument(
            project_id=project_id,
            character_bible_id=f"{project_id}:character_voice_bible:v1",
            segments=segments,
        )
        self._write_model(project_id, "director_doc.json", director)

    def _scan_candidates(self, text: str) -> list[CharacterCandidate]:
        speaker_evidence: dict[str, dict[str, object]] = {}
        for match in ATTRIBUTION_PATTERN.finditer(text):
            clause = match.group("clause")
            name = self._select_candidate_name(clause, text)
            if name is None:
                continue
            start = max(0, match.start() - 24)
            end = min(len(text), match.end() + 48)
            excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
            entry = speaker_evidence.setdefault(name, {"evidence": [], "explicit": False})
            evidence = entry["evidence"]
            if isinstance(evidence, list):
                evidence.append(excerpt)
            if match.group("introduces"):
                entry["explicit"] = True

        candidates: list[CharacterCandidate] = []
        for name, entry in speaker_evidence.items():
            evidence = entry["evidence"] if isinstance(entry["evidence"], list) else []
            explicit = bool(entry["explicit"])
            mentions = text.count(name)
            rejected = name in ROLE_TERMS or not self._looks_like_name(name) or (not explicit and mentions < 2)
            candidates.append(
                CharacterCandidate(
                    candidate_id=self._stable_id("candidate", name),
                    display_name=name,
                    decision="rejected" if rejected else "pending",
                    confidence=0.35 if rejected else min(0.98, 0.72 + len(evidence) * 0.04 + (0.08 if explicit else 0)),
                    mention_count=mentions,
                    dialogue_count=len(evidence),
                    evidence=evidence[:5],
                    reason="叙述短语或证据不足，不建立独立音色" if rejected else "检测到标点锚定的说话归属和直接对话证据",
                )
            )
        known_names = {candidate.display_name for candidate in candidates}
        for role in ROLE_TERMS:
            if role in text and role not in known_names:
                candidates.append(
                    CharacterCandidate(
                        candidate_id=self._stable_id("candidate", role),
                        display_name=role,
                        decision="rejected",
                        confidence=0.3,
                        mention_count=text.count(role),
                        dialogue_count=0,
                        evidence=[self._excerpt(text, role)],
                        reason="通用身份词，缺少具名角色证据",
                    )
                )
        accepted_names = [candidate.display_name for candidate in candidates if candidate.decision == "pending"]
        for candidate in candidates:
            if (
                candidate.decision == "rejected"
                and candidate.mention_count >= 2
                and any(
                    name != candidate.display_name
                    and self._alias_key(name).endswith(self._alias_key(candidate.display_name))
                    for name in accepted_names
                )
            ):
                candidate.decision = "pending"
                candidate.reason = "检测到已接纳全名的高频短称，将作为别名合并"
        return candidates

    @classmethod
    def _select_candidate_name(cls, clause: str, text: str) -> str | None:
        for prefix in LEADING_PHRASES:
            if clause.startswith(prefix) and len(clause) > len(prefix) + 1:
                clause = clause[len(prefix) :]
                break
        candidates = [clause[:length] for length in range(2, min(4, len(clause)) + 1)]
        candidates = [candidate for candidate in candidates if cls._looks_like_name(candidate)]
        if not candidates:
            return None
        counts = {candidate: text.count(candidate) for candidate in candidates}
        if max(counts.values(), default=0) <= 1:
            return max(candidates, key=len)
        shortest = min(candidates, key=len)
        baseline = counts[shortest]
        plausible = [
            candidate
            for candidate in candidates
            if counts[candidate] >= 2 and counts[candidate] >= baseline * 0.35
        ]
        return max(plausible or [shortest], key=len)

    @staticmethod
    def _looks_like_name(value: str) -> bool:
        if any(value.startswith(role) for role in ROLE_TERMS) or value.startswith(NON_NAME_PREFIXES):
            return False
        if value.endswith(NON_NAME_ENDINGS):
            return False
        return (
            value[0] in COMMON_SURNAMES
            or value.startswith(COMPOUND_SURNAMES)
            or value.endswith(NAME_SUFFIXES)
            or (len(value) >= 3 and value.startswith("小"))
        )

    @staticmethod
    def _alias_key(value: str) -> str:
        return value.translate(ALIAS_TRANSLATION)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        return [
            match.strip()
            for match in re.findall(r".+?(?:[。！？!?]+[”\"』」]?|$)", text, re.DOTALL)
            if match.strip()
        ]

    @staticmethod
    def _speaker_for_sentence(sentence: str, character_ids: dict[str, str]) -> str | None:
        for name in character_ids:
            if name != "旁白" and re.search(rf"{re.escape(name)}[\u4e00-\u9fff]{{0,8}}?{SPEECH_VERB}", sentence):
                return name
        return None

    @staticmethod
    def _excerpt(text: str, term: str) -> str:
        position = text.find(term)
        return re.sub(r"\s+", " ", text[max(0, position - 24) : position + len(term) + 48]).strip()

    def _find_source(self, project_id: str) -> Path:
        for path in self.source_root.glob("*.txt"):
            if self._project_id(path.name) == project_id:
                return path
        raise PreparationProblem(404, "未找到对应的小说源文件")

    def _source_summary(self, path: Path, encoding: Literal["utf-8", "gb18030"]) -> SourceSummary:
        project_id = self._project_id(path.name)
        return SourceSummary(
            project_id=project_id,
            file_name=path.name,
            display_name=path.stem,
            size_bytes=path.stat().st_size,
            encoding=encoding,
            status=self._status(project_id),
        )

    def _status(self, project_id: str) -> PreparationStatus:
        root = self.project_root / project_id
        if (root / "director_doc.json").is_file():
            return "director_ready"
        if (root / "character_voice_bible.json").is_file():
            return "characters_ready"
        if (root / "analysis_audit.json").is_file():
            return "analyzed"
        return "imported"

    def _read_text(self, path: Path) -> tuple[str, Literal["utf-8", "gb18030"]]:
        return self._decode(path.read_bytes())

    @staticmethod
    def _decode(content: bytes) -> tuple[str, Literal["utf-8", "gb18030"]]:
        try:
            return content.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            return content.decode("gb18030"), "gb18030"

    @staticmethod
    def _project_id(file_name: str) -> str:
        return PreparationService._stable_id("project", file_name.casefold())

    @staticmethod
    def _stable_id(prefix: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"

    def _artifact_path(self, project_id: str, file_name: str) -> Path:
        return self.project_root / project_id / file_name

    def _remove_artifact(self, project_id: str, file_name: str) -> None:
        self._artifact_path(project_id, file_name).unlink(missing_ok=True)

    def _write_model(self, project_id: str, file_name: str, model: BaseModel) -> None:
        path = self._artifact_path(project_id, file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _read_model(self, project_id: str, file_name: str, model_type: type[BaseModel]) -> BaseModel | None:
        path = self._artifact_path(project_id, file_name)
        if not path.is_file():
            return None
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def _require_model(
        self,
        project_id: str,
        file_name: str,
        model_type: type[BaseModel],
        detail: str,
    ) -> BaseModel:
        model = self._read_model(project_id, file_name, model_type)
        if model is None:
            raise PreparationProblem(409, detail)
        return model


def create_preparation_router(service: PreparationService) -> APIRouter:
    router = APIRouter()

    def handle(problem: PreparationProblem) -> HTTPException:
        return HTTPException(status_code=problem.status_code, detail=problem.detail)

    @router.get("/api/sources", response_model=list[SourceSummary])
    def list_sources() -> list[SourceSummary]:
        return service.list_sources()

    @router.post("/api/sources", response_model=SourceSummary, status_code=201)
    async def import_source(file: UploadFile = File(...)) -> SourceSummary:
        try:
            return service.import_source(file.filename or "", await file.read())
        except (PreparationProblem, UnicodeDecodeError) as problem:
            if isinstance(problem, UnicodeDecodeError):
                raise HTTPException(status_code=415, detail="TXT 编码必须是 UTF-8 或 GB18030") from problem
            raise handle(problem) from problem

    @router.get("/api/projects/{project_id}/preparation/preview", response_model=PreparationPreview)
    def preview(project_id: str) -> PreparationPreview:
        try:
            return service.preview(project_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/projects/{project_id}/preparation", response_model=PreparationPreview)
    def run(project_id: str, request: PreparationActionRequest) -> PreparationPreview:
        try:
            return service.run(project_id, request.action)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch(
        "/api/projects/{project_id}/references/{reference_id}",
        response_model=PreparationPreview,
    )
    def update_reference_selection(
        project_id: str,
        reference_id: str,
        request: ReferenceSelectionRequest,
    ) -> PreparationPreview:
        try:
            return service.update_reference_selection(project_id, reference_id, request.selected)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    return router
