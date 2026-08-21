from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator


LongFormMode = Literal["auto", "chapters", "characters"]
LongFormStrategy = Literal["short", "standard_chapters", "inferred_chapters", "characters"]
LongFormBatchState = Literal["pending", "analyzed", "characters_ready", "director_running", "ready", "failed"]
AnalysisBackend = Literal["local", "hybrid", "cloud", "rules"]

DEFAULT_LONG_TEXT_THRESHOLD = 120_000
DEFAULT_CHAPTERS_PER_BATCH = 50
DEFAULT_CHARACTERS_PER_BATCH = 50_000
DEFAULT_ANALYSIS_PARALLELISM = 4

STANDARD_CHAPTER_PATTERN = re.compile(
    r"^\s*第[零〇一二两三四五六七八九十百千万\d]+[章节回卷].*$",
    re.MULTILINE,
)
HEADING_MARKER_PATTERN = re.compile(
    r"^(?:序(?:章|言)?|楔子|引子|终章|尾声|后记|番外|"
    r"第?[零〇一二两三四五六七八九十百千万\d]+(?:章|节|回|卷|部|篇)|"
    r"(?:chapter|part|volume)\s*[\divxlcdm一二三四五六七八九十]+)",
    re.IGNORECASE,
)
NUMBERED_HEADING_PATTERN = re.compile(r"^(?:[零〇一二两三四五六七八九十百千万\d]+)[、.．\s]+\S+")
SENTENCE_BOUNDARY_PATTERN = re.compile(r"[。！？!?；;](?:[”’』」\"']*)|\n")


class LongFormAnalysisSettings(BaseModel):
    schema_version: int = 1
    mode: LongFormMode = "auto"
    long_text_threshold: int = Field(default=DEFAULT_LONG_TEXT_THRESHOLD, ge=20_000, le=2_000_000)
    chapters_per_batch: int = Field(default=DEFAULT_CHAPTERS_PER_BATCH, ge=5, le=200)
    characters_per_batch: int = Field(default=DEFAULT_CHARACTERS_PER_BATCH, ge=10_000, le=500_000)
    parallelism: int = Field(default=DEFAULT_ANALYSIS_PARALLELISM, ge=1, le=8)


class LongFormAnalysisSettingsUpdate(BaseModel):
    mode: LongFormMode | None = None
    long_text_threshold: int | None = Field(default=None, ge=20_000, le=2_000_000)
    chapters_per_batch: int | None = Field(default=None, ge=5, le=200)
    characters_per_batch: int | None = Field(default=None, ge=10_000, le=500_000)
    parallelism: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def require_change(self) -> "LongFormAnalysisSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("至少需要修改一项长篇分析设置")
        return self


class TextHeadingCandidate(BaseModel):
    candidate_id: str
    line_number: int
    start_char: int
    title: str = Field(min_length=1, max_length=80)
    score: int = Field(ge=0, le=10)


class TextStructureDraft(BaseModel):
    heading_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=240)
    backend: AnalysisBackend = "rules"
    model: str | None = None


class TextStructureSelection(BaseModel):
    heading_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=240)


class LongFormBatch(BaseModel):
    batch_id: str
    index: int = Field(ge=1)
    title: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    character_count: int = Field(ge=0)
    chapter_start: int | None = Field(default=None, ge=1)
    chapter_end: int | None = Field(default=None, ge=1)
    state: LongFormBatchState = "pending"
    candidate_ids: list[str] = Field(default_factory=list)
    new_character_count: int = Field(default=0, ge=0)
    reused_character_count: int = Field(default=0, ge=0)
    director_completed_passages: int = Field(default=0, ge=0)
    director_total_passages: int = Field(default=0, ge=0)


class LongFormPlan(BaseModel):
    schema_version: int = 1
    plan_id: str
    is_long_form: bool
    requested_mode: LongFormMode
    strategy: LongFormStrategy
    detection_backend: AnalysisBackend = "rules"
    detection_model: str | None = None
    total_characters: int = Field(ge=0)
    total_chapters: int = Field(ge=0)
    batches: list[LongFormBatch]
    warning: str | None = None


@dataclass(frozen=True)
class TextWindow:
    batch: LongFormBatch
    text: str


def find_heading_candidates(text: str, limit: int = 500) -> list[TextHeadingCandidate]:
    lines = text.splitlines(keepends=True)
    candidates: list[TextHeadingCandidate] = []
    offset = 0
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        line_start = offset + max(raw_line.find(stripped), 0)
        offset += len(raw_line)
        if not stripped or len(stripped) > 80 or STANDARD_CHAPTER_PATTERN.fullmatch(stripped):
            continue
        if stripped[-1:] in "。！？!?；;，,：:":
            continue
        previous_blank = index == 0 or not lines[index - 1].strip()
        next_blank = index + 1 >= len(lines) or not lines[index + 1].strip()
        score = int(previous_blank) + int(next_blank) + int(len(stripped) <= 32)
        if HEADING_MARKER_PATTERN.match(stripped):
            score += 4
        elif NUMBERED_HEADING_PATTERN.match(stripped):
            score += 3
        if score < 3:
            continue
        digest = hashlib.sha1(f"{line_start}:{stripped}".encode("utf-8")).hexdigest()[:10]
        candidates.append(
            TextHeadingCandidate(
                candidate_id=f"heading-{digest}",
                line_number=index + 1,
                start_char=line_start,
                title=stripped,
                score=min(score, 10),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def heuristic_heading_ids(candidates: list[TextHeadingCandidate]) -> list[str]:
    selected = [candidate.candidate_id for candidate in candidates if candidate.score >= 5]
    return selected if len(selected) >= 2 else []


def build_long_form_plan(
    text: str,
    settings: LongFormAnalysisSettings,
    *,
    inferred_candidates: list[TextHeadingCandidate] | None = None,
    inferred_structure: TextStructureDraft | None = None,
) -> LongFormPlan:
    standard_headings = [
        (match.start(), match.group(0).strip())
        for match in STANDARD_CHAPTER_PATTERN.finditer(text)
    ]
    is_long_form = len(text) >= settings.long_text_threshold
    requested_mode = settings.mode
    detection_backend: AnalysisBackend = "rules"
    detection_model: str | None = None
    warning: str | None = None

    if not is_long_form:
        batches = [_batch(1, "全文", 0, len(text), None, None)]
        strategy: LongFormStrategy = "short"
    elif requested_mode != "characters" and len(standard_headings) >= 2:
        batches = _chapter_batches(text, standard_headings, settings.chapters_per_batch)
        strategy = "standard_chapters"
    else:
        inferred_by_id = {candidate.candidate_id: candidate for candidate in inferred_candidates or []}
        inferred_headings: list[tuple[int, str]] = []
        if inferred_structure is not None:
            inferred_headings = sorted(
                {
                    (inferred_by_id[heading_id].start_char, inferred_by_id[heading_id].title)
                    for heading_id in inferred_structure.heading_ids
                    if heading_id in inferred_by_id
                }
            )
            detection_backend = inferred_structure.backend
            detection_model = inferred_structure.model
        if requested_mode != "characters" and len(inferred_headings) >= 2:
            batches = _chapter_batches(text, inferred_headings, settings.chapters_per_batch)
            strategy = "inferred_chapters"
        else:
            batches = _character_batches(text, settings.characters_per_batch)
            strategy = "characters"
            if requested_mode == "chapters":
                warning = "未确认出足够的章节标题，已按完整句边界切换为字数分批"

    digest_source = (
        f"{len(text)}:{settings.mode}:{settings.chapters_per_batch}:"
        f"{settings.characters_per_batch}:{strategy}:"
        f"{','.join(str(batch.start_char) for batch in batches)}"
    )
    plan_id = "long-form-" + hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:12]
    return LongFormPlan(
        plan_id=plan_id,
        is_long_form=is_long_form,
        requested_mode=requested_mode,
        strategy=strategy,
        detection_backend=detection_backend,
        detection_model=detection_model,
        total_characters=len(text),
        total_chapters=len(standard_headings) if strategy == "standard_chapters" else sum(
            batch.chapter_end - batch.chapter_start + 1
            for batch in batches
            if batch.chapter_start is not None and batch.chapter_end is not None
        ) if strategy == "inferred_chapters" else 0,
        batches=batches,
        warning=warning,
    )


def windows_from_plan(text: str, plan: LongFormPlan) -> list[TextWindow]:
    return [TextWindow(batch=batch, text=text[batch.start_char : batch.end_char]) for batch in plan.batches]


def _chapter_batches(
    text: str,
    headings: list[tuple[int, str]],
    chapters_per_batch: int,
) -> list[LongFormBatch]:
    batches: list[LongFormBatch] = []
    for start_index in range(0, len(headings), chapters_per_batch):
        end_index = min(start_index + chapters_per_batch, len(headings))
        start_char = 0 if start_index == 0 else headings[start_index][0]
        end_char = headings[end_index][0] if end_index < len(headings) else len(text)
        first_title = headings[start_index][1]
        last_title = headings[end_index - 1][1]
        title = first_title if first_title == last_title else f"{first_title} - {last_title}"
        batches.append(
            _batch(
                len(batches) + 1,
                title,
                start_char,
                end_char,
                start_index + 1,
                end_index,
            )
        )
    return batches


def _character_batches(text: str, target_size: int) -> list[LongFormBatch]:
    batches: list[LongFormBatch] = []
    start = 0
    while start < len(text):
        target = min(start + target_size, len(text))
        end = len(text) if target >= len(text) else _sentence_boundary(text, target)
        if end <= start:
            end = min(start + target_size, len(text))
        index = len(batches) + 1
        batches.append(_batch(index, f"第 {index} 批 · {start + 1:,}-{end:,} 字", start, end, index, index))
        start = end
    return batches or [_batch(1, "全文", 0, 0, None, None)]


def _sentence_boundary(text: str, target: int, search_radius: int = 3_000) -> int:
    forward_end = min(len(text), target + search_radius)
    forward = SENTENCE_BOUNDARY_PATTERN.search(text, target, forward_end)
    if forward is not None:
        return forward.end()
    backward_start = max(0, target - search_radius)
    matches = list(SENTENCE_BOUNDARY_PATTERN.finditer(text, backward_start, target))
    return matches[-1].end() if matches else target


def _batch(
    index: int,
    title: str,
    start_char: int,
    end_char: int,
    chapter_start: int | None,
    chapter_end: int | None,
) -> LongFormBatch:
    return LongFormBatch(
        batch_id=f"batch-{index:04d}",
        index=index,
        title=title,
        start_char=start_char,
        end_char=end_char,
        character_count=max(0, end_char - start_char),
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )
