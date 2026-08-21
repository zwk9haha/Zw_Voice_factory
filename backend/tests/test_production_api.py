from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.main import create_app


async def request(workspace_root: Path, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(workspace_root))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_quality_model_catalog_lists_gsv_v1_through_v4_and_indextts2(tmp_path: Path) -> None:
    response = asyncio.run(request(tmp_path, "GET", "/api/production/settings"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_quality_model"] == "gpt_sovits_v2"
    assert [item["model_id"] for item in payload["quality_models"]] == [
        "gpt_sovits_v1",
        "gpt_sovits_v2",
        "gpt_sovits_v2_pro",
        "gpt_sovits_v2_pro_plus",
        "gpt_sovits_v3",
        "gpt_sovits_v4",
        "indextts2",
    ]
    assert all(item["effect"] for item in payload["quality_models"])


def test_available_quality_model_selection_is_persisted(tmp_path: Path) -> None:
    pretrained = tmp_path / "models" / "tts_tools" / "gpt_sovits" / "GPT_SoVITS" / "pretrained_models"
    (pretrained / "gsv-v4-pretrained").mkdir(parents=True)
    (pretrained / "s1v3.ckpt").write_bytes(b"gpt")
    (pretrained / "gsv-v4-pretrained" / "s2Gv4.pth").write_bytes(b"sovits")

    updated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/production/settings",
            json={"selected_quality_model": "gpt_sovits_v4"},
        )
    )

    assert updated.status_code == 200
    assert updated.json()["selected_quality_model"] == "gpt_sovits_v4"
    restored = asyncio.run(request(tmp_path, "GET", "/api/production/settings"))
    assert restored.json()["selected_quality_model"] == "gpt_sovits_v4"


def test_quality_render_options_are_validated_and_persisted(tmp_path: Path) -> None:
    updated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/production/settings",
            json={
                "render_options": {
                    "chunk_length": 160,
                    "top_k": 24,
                    "top_p": 0.86,
                    "temperature": 0.72,
                    "repetition_penalty": 1.28,
                    "speed_factor": 1.08,
                    "fragment_interval": 0.22,
                    "batch_size": 2,
                    "split_bucket": True,
                    "seed": 42,
                    "emotion_strength": 0.8,
                }
            },
        )
    )

    assert updated.status_code == 200
    assert updated.json()["render_options"]["chunk_length"] == 160
    assert updated.json()["render_options"]["seed"] == 42
    restored = asyncio.run(request(tmp_path, "GET", "/api/production/settings"))
    assert restored.json()["render_options"] == updated.json()["render_options"]

    invalid = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/production/settings",
            json={"render_options": {"top_p": 1.5}},
        )
    )
    assert invalid.status_code == 422


def test_narrator_and_cache_policy_are_global_production_settings(tmp_path: Path) -> None:
    updated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/production/settings",
            json={
                "narrator_gender": "female",
                "auto_delete_played_cache": True,
                "cache_keep_sentences": 12,
            },
        )
    )

    assert updated.status_code == 200
    assert updated.json()["narrator_gender"] == "female"
    assert updated.json()["auto_delete_played_cache"] is True
    assert updated.json()["cache_keep_sentences"] == 12
    restored = asyncio.run(request(tmp_path, "GET", "/api/production/settings"))
    assert restored.json()["narrator_gender"] == "female"
    assert restored.json()["cache_keep_sentences"] == 12


def test_program_loudness_policy_defaults_and_persists(tmp_path: Path) -> None:
    default = asyncio.run(request(tmp_path, "GET", "/api/production/settings"))

    assert default.status_code == 200
    assert default.json()["loudness_policy"] == {
        "schema_version": 1,
        "enabled": True,
        "target_lufs": -18.0,
        "true_peak_dbtp": -1.0,
        "target_lra": 11.0,
        "max_segment_gain_db": 4.0,
    }

    updated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/production/settings",
            json={
                "loudness_policy": {
                    "enabled": True,
                    "target_lufs": -16.0,
                    "true_peak_dbtp": -1.5,
                    "target_lra": 9.0,
                    "max_segment_gain_db": 3.0,
                }
            },
        )
    )

    assert updated.status_code == 200
    assert updated.json()["loudness_policy"]["target_lufs"] == -16.0
    assert updated.json()["loudness_policy"]["max_segment_gain_db"] == 3.0
    restored = asyncio.run(request(tmp_path, "GET", "/api/production/settings"))
    assert restored.json()["loudness_policy"] == updated.json()["loudness_policy"]

    invalid = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/production/settings",
            json={"loudness_policy": {"target_lufs": -6.0}},
        )
    )
    assert invalid.status_code == 422
