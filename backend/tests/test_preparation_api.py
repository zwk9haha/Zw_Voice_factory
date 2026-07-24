import asyncio
from pathlib import Path

import httpx

from app.main import create_app


async def request(
    workspace_root: Path,
    method: str,
    path: str,
    **kwargs: object,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(workspace_root))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def import_source(workspace_root: Path, content: bytes, filename: str = "测试小说.txt") -> dict[str, object]:
    response = asyncio.run(
        request(
            workspace_root,
            "POST",
            "/api/sources",
            files={"file": (filename, content, "text/plain")},
        )
    )
    assert response.status_code == 201
    return response.json()


def run_action(workspace_root: Path, project_id: str, action: str) -> httpx.Response:
    return asyncio.run(
        request(
            workspace_root,
            "POST",
            f"/api/projects/{project_id}/preparation",
            json={"action": action},
        )
    )


def test_txt_upload_is_listed_with_detected_gb18030_encoding(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode("gb18030"))

    response = asyncio.run(request(tmp_path, "GET", "/api/sources"))

    assert response.status_code == 200
    assert response.json() == [
        {
            "project_id": imported["project_id"],
            "file_name": "测试小说.txt",
            "display_name": "测试小说",
            "size_bytes": 32,
            "encoding": "gb18030",
            "status": "imported",
        }
    ]


def test_analysis_preview_survives_a_new_app_instance(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])

    response = run_action(tmp_path, project_id, "analyze")
    assert response.status_code == 200

    preview = asyncio.run(request(tmp_path, "GET", f"/api/projects/{project_id}/preparation/preview"))
    audit = preview.json()["analysis_audit"]
    assert audit["engine"] == "rule_based_preview"
    assert audit["structure"]["chapter_count"] == 1
    assert audit["structure"]["character_count"] == 18


def test_character_extraction_separates_accepted_identity_from_false_positive(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"\n少年点了点头。\n药老问道：\"准备好了吗？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    assert response.status_code == 200
    payload = response.json()
    assert [character["display_name"] for character in payload["character_voice_bible"]["characters"]] == [
        "旁白",
        "萧炎",
        "药老",
    ]
    assert any(
        candidate["display_name"] == "少年" and candidate["decision"] == "rejected"
        for candidate in payload["analysis_audit"]["candidates"]
    )


def test_character_extraction_builds_reference_plan_from_importance(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "萧炎问道：\"准备好了吗？\"\n"
        "萧炎答道：\"那就出发。\"\n"
        "药老说道：\"且慢。\""
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    assert response.status_code == 200
    assert response.json()["reference_plan"]["automatic_threshold"] == 0.1
    items = response.json()["reference_plan"]["items"]
    by_name = {item["display_name"]: item for item in items}
    assert by_name["男旁白"]["selection_mode"] == "narrator_default"
    assert by_name["女旁白"]["selection_mode"] == "narrator_default"
    assert by_name["男旁白"]["selected"] is True
    assert by_name["女旁白"]["locked"] is True
    assert by_name["萧炎"]["selection_mode"] == "automatic"
    assert by_name["萧炎"]["selected"] is True
    assert by_name["药老"]["selection_mode"] == "automatic"
    assert by_name["药老"]["reuse_reference_id"] == by_name["男旁白"]["reference_id"]

    adjusted = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/reference-settings",
            json={"automatic_threshold": 0.75},
        )
    )
    assert adjusted.status_code == 200
    adjusted_by_name = {
        item["display_name"]: item
        for item in adjusted.json()["reference_plan"]["items"]
    }
    assert adjusted_by_name["萧炎"]["selection_mode"] == "automatic"
    assert adjusted_by_name["药老"]["selection_mode"] == "optional"
    assert adjusted_by_name["药老"]["selected"] is False


def test_only_optional_reference_items_can_be_toggled(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "萧炎问道：\"准备好了吗？\"\n"
        "萧炎答道：\"那就出发。\"\n"
        "纳兰嫣然说道：\"她会准时到场。\""
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    extracted = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/reference-settings",
            json={"automatic_threshold": 0.75},
        )
    ).json()
    by_name = {item["display_name"]: item for item in extracted["reference_plan"]["items"]}

    optional = by_name["纳兰嫣然"]
    assert optional["gender"] == "female"
    assert optional["reuse_reference_id"] == by_name["女旁白"]["reference_id"]
    selected = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{optional['reference_id']}",
            json={"selected": True},
        )
    )
    assert selected.status_code == 200
    selected_item = next(
        item for item in selected.json()["reference_plan"]["items"]
        if item["reference_id"] == optional["reference_id"]
    )
    assert selected_item["selected"] is True

    automatic = by_name["萧炎"]
    locked = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{automatic['reference_id']}",
            json={"selected": False},
        )
    )
    assert locked.status_code == 409


def test_reference_voice_prompt_updates_the_reference_and_character_bible(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(
        item for item in extracted["reference_plan"]["items"]
        if item["display_name"] == "萧炎"
    )

    response = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}",
            json={"voice_prompt": "青年男声，清亮克制，吐字自然，保持中性情绪"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    updated_reference = next(
        item for item in payload["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    updated_character = next(
        item for item in payload["character_voice_bible"]["characters"]
        if item["character_id"] == reference["source_character_id"]
    )
    assert updated_reference["voice_prompt"] == "青年男声，清亮克制，吐字自然，保持中性情绪"
    assert updated_character["voice_prompt"] == updated_reference["voice_prompt"]


def test_automatic_reference_can_be_toggled_after_the_selection_lock_is_disabled(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(
        item for item in extracted["reference_plan"]["items"]
        if item["display_name"] == "萧炎"
    )
    assert extracted["reference_plan"]["automatic_items_locked"] is True
    assert reference["locked"] is True

    unlocked = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/reference-settings",
            json={"automatic_items_locked": False},
        )
    )

    assert unlocked.status_code == 200
    unlocked_items = unlocked.json()["reference_plan"]["items"]
    unlocked_reference = next(item for item in unlocked_items if item["reference_id"] == reference["reference_id"])
    assert unlocked_reference["locked"] is False
    assert all(item["locked"] is True for item in unlocked_items if item["selection_mode"] == "narrator_default")

    deselected = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}",
            json={"selected": False},
        )
    )
    assert deselected.status_code == 200
    updated = next(
        item for item in deselected.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert updated["selection_mode"] == "automatic"
    assert updated["selected"] is False


def test_character_extraction_does_not_turn_speech_modifiers_into_names(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "\"谁知道这件事？\"萧炎苦涩的道。\n"
        "纳兰嫣然淡淡的道：\"我明白。\"\n"
        "想要知道答案并不容易，前途可以慢慢考虑。"
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    assert [character["display_name"] for character in response.json()["character_voice_bible"]["characters"]] == [
        "旁白",
        "萧炎",
        "纳兰嫣然",
    ]


def test_character_extraction_merges_variant_spelling_as_an_alias(tmp_path: Path) -> None:
    text = "第一章 初见\n萧薰儿说道：\"走吧。\"\n熏儿问道：\"萧炎哥哥呢？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    characters = response.json()["character_voice_bible"]["characters"]
    assert [character["display_name"] for character in characters] == ["旁白", "萧薰儿"]
    assert characters[1]["aliases"] == ["熏儿"]

    director = run_action(tmp_path, project_id, "generate_director").json()["director_doc"]
    alias_segment = next(segment for segment in director["segments"] if "熏儿问道" in segment["text"])
    assert alias_segment["character_id"] == characters[1]["character_id"]


def test_director_generation_references_stable_character_ids(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    run_action(tmp_path, project_id, "extract_characters")

    response = run_action(tmp_path, project_id, "generate_director")

    assert response.status_code == 200
    segments = response.json()["director_doc"]["segments"]
    characters = response.json()["character_voice_bible"]["characters"]
    identity_ids = {character["display_name"]: character["character_id"] for character in characters}
    character_ids = {segment["character_id"] for segment in segments}
    assert identity_ids["萧炎"] in character_ids
    assert identity_ids["药老"] in character_ids
    assert all(segment["segment_id"].startswith("seg-") for segment in segments)


def test_upload_rejects_non_txt_and_path_like_names(tmp_path: Path) -> None:
    wrong_type = asyncio.run(
        request(tmp_path, "POST", "/api/sources", files={"file": ("novel.md", b"text", "text/plain")})
    )
    unsafe_name = asyncio.run(
        request(tmp_path, "POST", "/api/sources", files={"file": ("../novel.txt", b"text", "text/plain")})
    )

    assert wrong_type.status_code == 415
    assert unsafe_name.status_code == 400
