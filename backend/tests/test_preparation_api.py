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
