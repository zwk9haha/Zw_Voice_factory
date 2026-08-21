from pathlib import Path

from app.long_form import (
    LongFormAnalysisSettings,
    LongFormAnalysisSettingsUpdate,
    TextStructureDraft,
    build_long_form_plan,
    find_heading_candidates,
    heuristic_heading_ids,
    windows_from_plan,
)
from app.preparation import PreparationService


def test_standard_chapters_are_grouped_without_losing_source_text() -> None:
    text = "\n".join(
        f"第{index}章 测试章节\n这是第{index}章的完整正文。"
        for index in range(1, 121)
    )
    settings = LongFormAnalysisSettings(
        mode="auto",
        long_text_threshold=20_000,
        chapters_per_batch=50,
    )
    text = text + "\n" + ("补充正文。" * 4_000)

    plan = build_long_form_plan(text, settings)
    windows = windows_from_plan(text, plan)

    assert plan.strategy == "standard_chapters"
    assert [(batch.chapter_start, batch.chapter_end) for batch in plan.batches] == [(1, 50), (51, 100), (101, 120)]
    assert "".join(window.text for window in windows) == text


def test_character_batches_extend_to_a_complete_sentence_boundary() -> None:
    sentence = "他很难会主动要求谁给自己买什么东西，即使，是萧炎也一样。"
    text = sentence * 1_200
    settings = LongFormAnalysisSettings(
        mode="characters",
        long_text_threshold=20_000,
        characters_per_batch=10_000,
    )

    plan = build_long_form_plan(text, settings)
    windows = windows_from_plan(text, plan)

    assert plan.strategy == "characters"
    assert len(windows) > 1
    assert all(window.text.endswith("。") for window in windows)
    assert "".join(window.text for window in windows) == text


def test_nonstandard_heading_candidates_require_a_consistent_sequence() -> None:
    text = "\n\n一、风起\n\n正文。\n\n二、归途\n\n正文。\n\n这只是普通短句\n正文。"

    candidates = find_heading_candidates(text)
    selected = heuristic_heading_ids(candidates)

    titles = [candidate.title for candidate in candidates if candidate.candidate_id in selected]
    assert titles == ["一、风起", "二、归途"]


def test_batch_local_importance_preserves_arc_specific_characters(tmp_path: Path) -> None:
    service = PreparationService(tmp_path)
    chapters: list[str] = []
    for index in range(1, 11):
        speaker = "药老" if index <= 5 else "萧炎"
        dialogue = "\n".join(f'{speaker}说道："第{turn}次确认。"' for turn in range(8))
        chapters.append(f"第{index}章 测试\n{dialogue}\n" + "风声掠过长街。" * 300)
    imported = service.import_source("长篇局部权重.txt", "\n".join(chapters).encode("utf-8"))
    service.update_analysis_settings(
        imported.project_id,
        LongFormAnalysisSettingsUpdate(
            mode="chapters",
            long_text_threshold=20_000,
            chapters_per_batch=5,
            parallelism=4,
        ),
    )

    preview = service.run(imported.project_id, "analyze")

    assert preview.analysis_audit is not None
    assert preview.analysis_audit.long_form_plan is not None
    assert len(preview.analysis_audit.long_form_plan.batches) == 2
    candidates = {candidate.display_name: candidate for candidate in preview.analysis_audit.candidates}
    assert candidates["药老"].local_importance == 0.95
    assert candidates["萧炎"].local_importance == 0.95
    assert preview.analysis_settings.parallelism == 4
    activity = service.analysis_activity(imported.project_id)
    assert activity.started_at is not None
    assert activity.completed_at is not None
    assert activity.elapsed_seconds >= 0
