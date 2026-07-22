from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .domain import CharacterTier, ReviewStatus


app = FastAPI(title="Zw Voice Factory API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "zw-voice-factory"}


@app.get("/api/workspace")
def workspace() -> dict:
    return {
        "project": {"id": "doupo_demo", "name": "斗破苍穹", "route": "quality"},
        "summary": {"characters": 5, "accepted_references": 2, "segments": 500, "generated": 3},
        "characters": [
            {
                "character_id": "narrator",
                "display_name": "旁白",
                "tier": CharacterTier.core,
                "importance": 1.0,
                "voice_prompt": "成熟、清晰、稳定的男声，叙述克制，具有空间感",
                "reference_status": ReviewStatus.accepted,
                "emotion_variants": ["neutral", "solemn", "tense"],
                "color": "teal",
            },
            {
                "character_id": "xiao_yan",
                "display_name": "萧炎",
                "tier": CharacterTier.core,
                "importance": 0.94,
                "voice_prompt": "青年男声，清亮但有韧劲，克制中保留爆发力",
                "reference_status": ReviewStatus.accepted,
                "emotion_variants": ["neutral", "angry", "sad"],
                "color": "violet",
            },
            {
                "character_id": "test_officer",
                "display_name": "测验员",
                "tier": CharacterTier.supporting,
                "importance": 0.42,
                "voice_prompt": "中年男声，冷淡、清晰、公式化",
                "reference_status": ReviewStatus.pending,
                "emotion_variants": [],
                "color": "gold",
            },
        ],
        "segments": [
            {"segment_id": "s001", "character_id": "narrator", "speaker": "旁白", "emotion": "tense", "text": "望着测验魔石碑上闪亮的五个大字，少年面无表情。"},
            {"segment_id": "s002", "character_id": "test_officer", "speaker": "测验员", "emotion": "cold", "text": "萧炎，斗之力，三段。级别，低级。"},
            {"segment_id": "s003", "character_id": "xiao_yan", "speaker": "萧炎", "emotion": "restrained", "text": "三十年河东，三十年河西，莫欺少年穷。"},
        ],
    }
