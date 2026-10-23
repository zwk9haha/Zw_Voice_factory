from __future__ import annotations

import json
import shutil
import threading
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .jobs import JobProblem, JobRequest, JobService, JobStatus


CatalogVersionStatus = Literal["queued", "running", "complete", "failed"]
CatalogVersionSource = Literal["generated", "derived", "uploaded", "recorded"]


class VoiceCatalogPreset(BaseModel):
    preset_id: str
    display_name: str
    description: str
    cfg_value: float = Field(ge=0.1, le=10)
    inference_timesteps: int = Field(ge=1, le=100)
    target_duration_seconds: float = Field(default=10.0, ge=1, le=30)


class VoiceCatalogReferenceText(BaseModel):
    text_id: str
    display_name: str
    purpose: str
    text: str = Field(min_length=1, max_length=220)


class VoiceCatalogVersion(BaseModel):
    version_id: str
    source: CatalogVersionSource
    parent_version_id: str | None = None
    variant_name: str = "基础参考"
    variant_description: str = ""
    emotion: str | None = None
    intensity: float | None = Field(default=None, ge=0, le=1)
    status: CatalogVersionStatus
    audio_url: str | None = None
    duration_seconds: float | None = None
    job_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class VoiceCatalogAsset(BaseModel):
    asset_id: str
    display_name: str
    category: str
    gender: Literal["female", "male"]
    age_range: Literal["young_adult", "adult", "middle_aged", "senior"]
    voice_prompt: str = Field(min_length=1, max_length=1_000)
    timbre_tags: list[str] = Field(max_length=8)
    delivery_tags: list[str] = Field(max_length=8)
    voice_constraints: list[str] = Field(max_length=6)
    use_cases: list[str] = Field(max_length=8)
    rationale: str = ""
    confidence: float = Field(default=0.9, ge=0, le=1)
    reference_text: str = Field(min_length=1, max_length=220)
    generation_preset_id: str = "quality_voice_catalog"
    active_version_id: str | None = None
    versions: list[VoiceCatalogVersion] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class VoiceCatalogPayload(BaseModel):
    presets: list[VoiceCatalogPreset]
    reference_texts: list[VoiceCatalogReferenceText]
    assets: list[VoiceCatalogAsset]


class VoiceCatalogMatch(BaseModel):
    """A ranked, explainable catalog candidate for workflow consumers."""

    asset_id: str
    display_name: str
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list, max_length=8)
    version_id: str | None = None
    audio_url: str | None = None
    duration_seconds: float | None = None
    ready: bool = False


class VoiceCatalogReference(BaseModel):
    """A fully resolved immutable catalog version ready for rendering."""

    asset_id: str
    display_name: str
    version_id: str
    audio_url: str
    duration_seconds: float | None = None
    reference_text: str
    voice_prompt: str


class VoiceCatalogAssetUpdate(BaseModel):
    voice_prompt: str | None = Field(default=None, min_length=1, max_length=1_000)
    timbre_tags: list[str] | None = Field(default=None, max_length=8)
    delivery_tags: list[str] | None = Field(default=None, max_length=8)
    voice_constraints: list[str] | None = Field(default=None, max_length=6)
    use_cases: list[str] | None = Field(default=None, max_length=8)
    reference_text: str | None = Field(default=None, min_length=1, max_length=220)
    generation_preset_id: str | None = Field(default=None, max_length=80)


class VoiceCatalogGenerateRequest(BaseModel):
    reference_text: str | None = Field(default=None, min_length=1, max_length=220)
    voice_prompt: str | None = Field(default=None, min_length=1, max_length=1_000)
    preset_id: str | None = Field(default=None, max_length=80)


class VoiceCatalogDerivativeRequest(BaseModel):
    parent_version_id: str = Field(min_length=1, max_length=120)
    variant_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    emotion: str | None = Field(default=None, max_length=80)
    intensity: float = Field(default=0.45, ge=0, le=1)
    reference_text: str | None = Field(default=None, min_length=1, max_length=220)
    preset_id: str | None = Field(default=None, max_length=80)


class VoiceCatalogMatchRequest(BaseModel):
    gender: str | None = Field(default=None, max_length=40)
    age_range: str | None = Field(default=None, max_length=80)
    timbre_tags: list[str] = Field(default_factory=list, max_length=8)
    delivery_tags: list[str] = Field(default_factory=list, max_length=8)
    use_cases: list[str] = Field(default_factory=list, max_length=8)
    voice_prompt: str | None = Field(default=None, max_length=1_000)
    limit: int = Field(default=8, ge=1, le=32)
    require_ready: bool = False


class VoiceCatalogProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


REFERENCE_TEXTS = [
    VoiceCatalogReferenceText(
        text_id="neutral_story",
        display_name="中性叙述 · 默认横向比较",
        purpose="覆盖自然停顿、陈述句和句尾收束，适合比较基础音色。",
        text="雨后的长街渐渐安静下来，我停在灯影交界处，听见远处的风，然后平静地说：今天的路，还要继续走下去。",
    ),
    VoiceCatalogReferenceText(
        text_id="question_and_pause",
        display_name="疑问停连 · 表达检查",
        purpose="检查疑问句、短停顿和句尾变化是否自然。",
        text="你也听见了吗？先别急着回答，等风停下来，我们再仔细看看，前面究竟藏着什么。",
    ),
    VoiceCatalogReferenceText(
        text_id="light_emotion",
        display_name="轻情绪 · 动态检查",
        purpose="检查轻微惊讶、安慰与收束，不把音色锁死在单一情绪。",
        text="原来你真的来了。没关系，慢慢说就好；无论结果怎样，我们都还有一次重新出发的机会。",
    ),
]


PRESETS = [
    VoiceCatalogPreset(
        preset_id="quality_voice_catalog",
        display_name="质量优先",
        description="默认方案。更高推理步数，优先自然度、稳定性和可复用性。",
        cfg_value=2.4,
        inference_timesteps=24,
    ),
    VoiceCatalogPreset(
        preset_id="stable_voice_catalog",
        display_name="稳定优先",
        description="减少随机探索，适合先建立可长期复用的基准音色。",
        cfg_value=2.0,
        inference_timesteps=18,
    ),
    VoiceCatalogPreset(
        preset_id="explore_voice_catalog",
        display_name="音色探索",
        description="保留更多变化，适合从同一声线探索不同表达方向。",
        cfg_value=2.8,
        inference_timesteps=30,
    ),
]


DETAILED_VOICE_NOTES: dict[str, str] = {
    "female-young-clear-natural": "保持成年女性的自然核心，不做刻意少女化。声音从口腔前部发出，明亮但不尖，气息只做轻微支撑；句中保持清晰连贯，句尾平稳落地，遇到叙事重点时只增加一点力度，不使用夸张颤音。",
    "female-young-sweet-lively": "甜美来自明亮共鸣和灵活语调，而不是撒娇或童声。短句可以有轻微上扬和更快的反应速度，重音落在关键词上，笑意只保留在音色边缘；长句仍要保持字头清楚、呼吸自然，不能尖薄刺耳。",
    "female-young-gentle-light": "以柔和、轻盈和有温度为核心，气息像贴着语句流动，不能虚浮或漏气。声线保持清楚的核心音，安慰和抒情内容适当放慢，句尾轻轻收束；面对紧张内容也只增加内在支撑，不突然变成哭腔。",
    "female-young-cool-controlled": "清冷感来自较少的装饰、平直的语调和稳定的中高音区，不是冷漠或机械。保持口腔共鸣清楚、气息内收，重点词用短促而干净的咬字强调；情绪变化以停顿长度和力度微调表达，避免过度气声与刻意压低。",
    "female-young-intellectual": "知性稳重需要清晰的逻辑重音和均匀的气息支持。按照信息层级组织停连，陈述句不拖尾，疑问句保持自然疑问感；声线明晰但不能变成新闻播音，遇到人物情绪时保留生活化的微小波动。",
    "female-young-airy": "保留轻柔通透的空气感，但每个字都要有可辨识的核心音。使用较轻的声带接触和自然口腔共鸣，静态段落舒缓、句间留出呼吸空间；避免持续耳语、过长气声和虚弱感，不能牺牲可懂度换取梦幻效果。",
    "female-adult-warm-mature": "成熟感来自更稳定的胸腔支撑、较圆的元音和从容的语速。声线要亲切可靠，像有生活经验的人在面对面讲话；重点词增加厚度而不是音量，安慰、解释和叙述都保持平和，避免老年化、过度柔弱或播音腔。",
    "female-adult-low-calm": "低沉但仍然保持女性声线的柔韧和清晰，不能通过挤压喉咙制造低音。胸腔共鸣承担稳定感，咬字短而准确，句间停顿有判断意味；情绪压力通过更紧的节奏和更明确的重音呈现，不用吼叫或过度冷漠。",
    "female-adult-brisk-sharp": "明快利落来自清楚的辅音、较短的停顿和有弹性的中高音，不是持续加速。语气可以直接、果断，句尾少拖延；面对复杂信息先保证分句清楚，再用轻微动态表达行动感，避免尖锐、急促含混和过度年轻化。",
    "female-adult-rough-weathered": "沙哑只作为质地，不应像喉部损伤。保留清晰的基频和稳定的气息，让磨砂感出现在辅音边缘和句尾；经历感通过放慢、克制和少量停顿表达，情绪越重越不能牺牲字词可懂度，避免持续压嗓。",
    "female-middle-steady-trustworthy": "沉稳可信需要朴素、连续和不抢戏的声音表现。以自然胸腔共鸣支撑较慢语速，句子不急着表现情绪，重点处只做轻微加重；保留日常生活感和呼吸痕迹，避免刻意老年声、虚弱声以及过度端庄。",
    "female-middle-kind-wise": "慈和睿智来自温润的共鸣、耐心的停顿和柔和但明确的句尾。像在照顾晚辈时讲话，语速舒缓却不含糊，给建议时有分寸、不说教；情绪可以有微笑和回忆感，但不能飘成神秘旁白或过度老态。",
    "male-young-clear-youthful": "少年感建立在清亮的男性核心和较轻的声音重量上，不使用童声或尖细假声。保持自然的呼吸与灵活的语调，日常句子亲近，行动句略有弹性；关键字清楚、句尾不拖，紧张时增加速度而不是喊叫。",
    "male-young-gentle-natural": "以温和、真诚和自然的男性中低音为主，胸腔共鸣适中，不能过分磁性或油腻。像真实人物面对面说话，语速平稳，情绪通过轻微的音高和力度变化表达；对白保留自然停顿，叙述保持干净的连续性。",
    "male-young-bright-lively": "爽朗活泼来自充足气息、明亮共鸣和有弹性的节奏，不等于持续大声。短句反应快、重音鲜明，笑意和行动感点到即止；长句要主动分段，避免吵闹、喊叫、字尾上飘和年轻化过度。",
    "male-young-low-calm": "低沉冷静以稳定的胸腔共鸣和收敛的动态为基础，声音可以厚但不能闷。语速略慢、停顿有目的，结论句的句尾稳而短；压力场景保持气息和咬字，不通过压喉或过度老成来制造威严。",
    "male-young-controlled-cool": "冷峻克制使用偏硬的质地、明确的辅音和较少的语调装饰。角色不主动解释情绪，信息靠停顿、短重音和干净句尾传递；保持自然呼吸和青年声线，避免机械平读、持续压低以及完全没有人味的冷感。",
    "male-young-heroic-strong": "热血有力需要宽一些的胸腔共鸣、足够气息和更大的动态范围，但所有字词必须清楚。行动和宣告句可以增加力度与速度，平静段落立即回收音量；情绪高点用支撑和重音，不用吼叫、破音或连续夸张。",
    "male-adult-steady-solid": "沉稳厚实以稳定的低中音、宽而自然的共鸣和从容的气息为核心。像有责任感的成年人在清楚说明事情，重点词有重量但不压迫；句尾保持落地，复杂叙述按逻辑分层，避免老年感、广告腔和过度磁性。",
    "male-adult-warm-mature": "温暖成熟来自圆润的元音、均匀的气息和适度偏重的声音重量。表达包容、可靠，陪伴式对白可以更柔和，叙述仍保持清晰；情绪不靠甜腻或过度低沉推动，避免播音腔、油腻感和男性厚度流失。",
    "male-adult-magnetic-natural": "自然磁性来自集中、顺滑的共鸣和松弛的发声状态，不靠压喉或刻意拖长句尾。保持轻微的魅力和从容，关键词清楚、节奏自然；面对严肃内容收起油滑感，避免广告腔、过度气声和套路化低音。",
    "male-middle-authoritative": "权威感建立在深而稳定的胸腔共鸣、充分气息和明确的句法重音上。语速偏慢但绝不拖沓，命令或判断句的句尾有控制力；叙事段落保持沉着与清晰，避免吼叫、过度苍老和沉闷含混。",
    "male-middle-rough-weathered": "粗粝沧桑作为长期经历留下的质地，必须保留稳定基频和字词清晰度。声音重量偏重，辅音边缘略带磨损，停顿比速度更能表现疲惫；情绪保持克制，避免把沙哑做成损伤音、咳嗽感或完全听不清。",
    "male-middle-kind-wise": "慈祥睿智以温厚的中低音、从容的呼吸和耐心的停连表达。像经验丰富的长辈分享判断，不急于压过别人，解释时字词清楚、建议有温度；保留生活感与男性厚度，避免说教、过度老态和神秘化。",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_assets() -> list[VoiceCatalogAsset]:
    default_text = REFERENCE_TEXTS[0].text
    definitions = [
        ("female-young-clear-natural", "女青年·清亮自然", "female", "young_adult", "成年女性，青年感自然，音域中高，声线清亮干净，重量适中偏轻，混合共鸣，气息柔和，咬字清晰，语速平稳，表达自然，不要过甜，不要播音腔。", ["中高音", "清亮", "干净", "轻盈"], ["咬字清晰", "语速平稳", "停连自然", "动态适中"], ["避免过甜", "避免刻意年轻化", "保持口语自然"], ["女旁白", "女主角", "普通配角"]),
        ("female-young-sweet-lively", "女青年·甜美活泼", "female", "young_adult", "成年女性，青年感明显，音域中高，声线明亮柔软，质地细腻，气息轻快，共鸣靠前，咬字灵活，语速偏快，表达有亲和力但不幼稚。", ["明亮", "柔软", "细腻", "轻快"], ["语速偏快", "语调灵活", "亲和自然", "活泼"], ["避免童声", "避免过度撒娇", "避免尖锐"], ["少女角色", "轻喜剧", "活泼配角"]),
        ("female-young-gentle-light", "女青年·温柔轻盈", "female", "young_adult", "成年女性，青年感适中，音域中高，声线柔和细腻，重量偏轻，气息流动自然，共鸣温暖，咬字清楚，语速舒缓，情绪克制而有温度。", ["柔和", "细腻", "轻盈", "温暖"], ["语速舒缓", "气息连贯", "安静亲和", "句尾柔和"], ["避免虚弱", "避免过度气声", "保留清晰度"], ["温柔女主", "情感旁白", "治愈场景"]),
        ("female-young-cool-controlled", "女青年·清冷克制", "female", "young_adult", "成年女性，青年感自然，音域中高偏低，声线清冷干净，重量适中，质地偏薄而稳定，胸腔共鸣适度，咬字利落，语速平稳，情绪收敛。", ["清冷", "干净", "偏薄", "稳定"], ["咬字利落", "语气克制", "停连明确", "低动态"], ["避免冷漠机械", "避免刻意压低", "避免气息过重"], ["清冷女主", "理性角色", "悬疑旁白"]),
        ("female-young-intellectual", "女青年·知性稳重", "female", "young_adult", "成年女性，青年感与成熟度平衡，音域中高，声线明晰温和，重量适中，混合共鸣，气息稳定，吐字准确，语速中等，表达理性可靠。", ["明晰", "温和", "适中", "可靠"], ["吐字准确", "语速中等", "逻辑清楚", "动态平稳"], ["避免播音腔", "避免过度端庄", "保持生活感"], ["知识旁白", "理性女主", "说明性文本"]),
        ("female-young-airy", "女青年·空气感柔和", "female", "young_adult", "成年女性，青年感自然，音域中高，声线轻柔通透，空气感明显但核心清楚，重量偏轻，口腔共鸣自然，语速舒缓，表达细腻。", ["通透", "空气感", "轻柔", "细腻"], ["轻声自然", "语速舒缓", "情绪细腻", "停顿柔和"], ["避免虚浮", "避免听不清", "避免过多气声"], ["梦境旁白", "温柔配角", "抒情场景"]),
        ("female-adult-warm-mature", "成年女性·温暖成熟", "female", "adult", "成年女性，成熟度明显，音域中低，声线温暖饱满，重量适中，胸腔与口腔共鸣均衡，气息稳定，咬字清楚，语速平稳，表达亲切可靠。", ["温暖", "饱满", "中低音", "成熟"], ["语速平稳", "亲切可靠", "动态适中", "句尾稳"], ["避免老年感", "避免过度柔弱", "避免播音腔"], ["成年女主", "家庭旁白", "可靠配角"]),
        ("female-adult-low-calm", "成年女性·低沉冷静", "female", "adult", "成年女性，成熟度高，音域中低，声线低沉清晰，重量偏重，胸腔共鸣稳定，质地干净，语速偏慢，表达冷静有判断力。", ["低沉", "清晰", "偏重", "冷静"], ["语速偏慢", "判断明确", "情绪收束", "停顿有力"], ["避免男性化", "避免刻意压嗓", "保留女性共鸣"], ["冷静女上司", "悬疑角色", "权威旁白"]),
        ("female-adult-brisk-sharp", "成年女性·明快利落", "female", "adult", "成年女性，成熟度适中，音域中高，声线明亮有弹性，重量适中，咬字利落，语速偏快，停连简洁，表达直接果断。", ["明亮", "有弹性", "利落", "直接"], ["语速偏快", "咬字利落", "节奏清楚", "果断"], ["避免尖锐", "避免急促含混", "避免年轻化过度"], ["职场角色", "行动派女主", "轻喜剧"]),
        ("female-adult-rough-weathered", "成年女性·沙哑沧桑", "female", "adult", "成年女性，成熟度高，音域中低，声线带轻微沙哑与磨砂质地，重量适中偏重，胸腔共鸣明显，语速平稳，情绪有历练感。", ["沙哑", "磨砂", "中低音", "沧桑"], ["语速平稳", "历练感", "情绪克制", "句尾沉着"], ["避免损伤感", "避免过度沙哑", "保证字词清晰"], ["沧桑角色", "末世旁白", "疲惫长者"]),
        ("female-middle-steady-trustworthy", "中老年女性·沉稳可信", "female", "middle_aged", "中老年女性，成熟度高，音域中低，声线稳定厚实，质地朴素，胸腔共鸣自然，气息从容，吐字清楚，语速偏慢，具有可信赖的生活感。", ["厚实", "朴素", "稳定", "生活感"], ["语速偏慢", "从容", "可信", "叙述清楚"], ["避免刻意苍老", "避免虚弱", "保持自然"], ["长辈角色", "生活旁白", "村镇人物"]),
        ("female-middle-kind-wise", "中老年女性·慈和睿智", "female", "middle_aged", "中老年女性，成熟度高，音域中低，声线温润沉静，重量适中，气息柔和，共鸣温暖，咬字清楚，语速舒缓，表达慈和而有智慧。", ["温润", "沉静", "温暖", "睿智"], ["语速舒缓", "慈和", "耐心", "句尾柔和"], ["避免神秘虚浮", "避免过度老态", "保留清晰度"], ["祖辈角色", "治愈旁白", "智者女性"]),
        ("male-young-clear-youthful", "男青年·清亮少年感", "male", "young_adult", "成年男性，青年感偏强，音域中高，声线清亮自然，重量偏轻，质地干净，胸腔共鸣适度，咬字清楚，语速中等，保持少年感但不幼稚。", ["清亮", "干净", "轻盈", "少年感"], ["语速中等", "自然亲和", "咬字清楚", "动态灵活"], ["避免童声", "避免尖细", "保留男性核心"], ["少年主角", "青春旁白", "普通男配角"]),
        ("male-young-gentle-natural", "男青年·温和自然", "male", "young_adult", "成年男性，青年感自然，音域中低，声线温和清晰，重量适中，质地平滑，口腔与胸腔共鸣均衡，语速平稳，表达真诚克制。", ["温和", "清晰", "平滑", "自然"], ["语速平稳", "真诚", "停连自然", "情绪克制"], ["避免过度磁性", "避免播音腔", "避免轻浮"], ["男主角", "情感旁白", "日常对话"]),
        ("male-young-bright-lively", "男青年·爽朗活泼", "male", "young_adult", "成年男性，青年感明显，音域中高，声线明亮有弹性，重量适中，气息充足，咬字清爽，语速偏快，表达外向热情。", ["明亮", "有弹性", "爽朗", "充足"], ["语速偏快", "外向", "节奏鲜明", "情绪饱满"], ["避免吵闹", "避免喊叫", "避免幼稚"], ["热闹配角", "冒险主角", "轻喜剧"]),
        ("male-young-low-calm", "男青年·低沉冷静", "male", "young_adult", "成年男性，青年感与成熟度平衡，音域中低，声线低沉干净，重量偏重，共鸣集中，气息稳定，语速偏慢，表达冷静自持。", ["低沉", "干净", "偏重", "集中"], ["语速偏慢", "冷静", "节奏稳定", "低动态"], ["避免压嗓", "避免含混", "避免过度老成"], ["冷静男主", "战斗旁白", "理性角色"]),
        ("male-young-controlled-cool", "男青年·冷峻克制", "male", "young_adult", "成年男性，青年感自然，音域中低，声线冷峻清晰，质地偏硬，重量适中偏重，胸腔共鸣明确，咬字利落，语速平稳，情绪收束。", ["冷峻", "清晰", "偏硬", "克制"], ["咬字利落", "情绪收束", "停顿明确", "语速平稳"], ["避免机械", "避免过度压低", "保持自然呼吸"], ["冷面主角", "反派青年", "悬疑角色"]),
        ("male-young-heroic-strong", "男青年·热血有力", "male", "young_adult", "成年男性，青年感与力量感并存，音域中低，声线明亮厚实，重量偏重，气息充足，胸腔共鸣明显，咬字有力，动态较大但不喊叫。", ["厚实", "有力", "明亮", "力量感"], ["动态较大", "咬字有力", "节奏鲜明", "情绪直接"], ["避免喊叫", "避免过度夸张", "保留字词清晰"], ["热血主角", "战斗场景", "英雄旁白"]),
        ("male-adult-steady-solid", "成年男性·沉稳厚实", "male", "adult", "成年男性，成熟度明显，音域中低，声线厚实稳定，重量偏重，胸腔共鸣自然，气息从容，咬字清楚，语速平稳，表达可信而有分寸。", ["厚实", "稳定", "中低音", "可信"], ["语速平稳", "从容", "分寸感", "句尾稳"], ["避免老年感", "避免过度磁性", "保持口语自然"], ["成年男主", "成熟旁白", "领导角色"]),
        ("male-adult-warm-mature", "成年男性·温暖成熟", "male", "adult", "成年男性，成熟度高，音域中低，声线温暖圆润，重量适中偏重，质地平滑，共鸣宽，语速舒缓，表达包容可靠。", ["温暖", "圆润", "平滑", "宽共鸣"], ["语速舒缓", "包容", "可靠", "情绪柔和"], ["避免过度温柔", "避免播音腔", "保持男性厚度"], ["成熟男主", "陪伴旁白", "家庭角色"]),
        ("male-adult-magnetic-natural", "成年男性·磁性自然", "male", "adult", "成年男性，成熟度适中，音域中低，声线有自然磁性，重量适中，质地顺滑，共鸣集中，气息稳定，咬字清晰，表达松弛但不油腻。", ["自然磁性", "顺滑", "集中", "松弛"], ["表达松弛", "咬字清晰", "节奏自然", "亲和"], ["避免油腻", "避免刻意压嗓", "避免广告腔"], ["魅力角色", "都市男主", "商业旁白"]),
        ("male-middle-authoritative", "中老年男性·浑厚权威", "male", "middle_aged", "中老年男性，成熟度高，音域低，声线浑厚稳定，重量偏重，胸腔共鸣深，气息充足，语速偏慢，咬字有力，表达具有权威与控制感。", ["浑厚", "低沉", "深共鸣", "权威"], ["语速偏慢", "咬字有力", "控制感", "句尾沉着"], ["避免吼叫", "避免过度苍老", "保持清晰度"], ["长者权威", "帝王角色", "史诗旁白"]),
        ("male-middle-rough-weathered", "中老年男性·粗粝沧桑", "male", "middle_aged", "中老年男性，成熟度高，音域低，声线粗粝带沙哑，重量偏重，胸腔共鸣深，质地有磨损感，语速平稳偏慢，表达有经历感。", ["粗粝", "沙哑", "低沉", "磨损感"], ["语速偏慢", "经历感", "情绪克制", "叙述沉着"], ["避免损伤感", "避免过度沙哑", "保证可懂度"], ["老兵角色", "末世人物", "沧桑旁白"]),
        ("male-middle-kind-wise", "中老年男性·慈祥睿智", "male", "middle_aged", "中老年男性，成熟度高，音域中低，声线温厚平和，重量适中偏重，共鸣温暖，气息从容，语速舒缓，表达慈祥、耐心而有智慧。", ["温厚", "平和", "温暖", "睿智"], ["语速舒缓", "耐心", "慈祥", "句尾柔和"], ["避免说教", "避免过度老态", "保持生活感"], ["长辈角色", "导师角色", "治愈旁白"]),
    ]
    assets: list[VoiceCatalogAsset] = []
    created_at = _now()
    for asset_id, display_name, gender, age_range, prompt, timbre, delivery, constraints, uses in definitions:
        detailed_prompt = f"{prompt} {DETAILED_VOICE_NOTES[asset_id]}"
        assets.append(
            VoiceCatalogAsset(
                asset_id=asset_id,
                display_name=display_name,
                category=f"{gender}_{age_range}",
                gender=gender,
                age_range=age_range,
                voice_prompt=detailed_prompt,
                timbre_tags=timbre,
                delivery_tags=delivery,
                voice_constraints=constraints,
                use_cases=uses,
                rationale=f"核心方向：{display_name}。重点保留{timbre[0]}、{delivery[0]}和{delivery[1]}，适用于{uses[0]}、{uses[1]}；生成时优先维持身份稳定、字词可懂和自然口语感。",
                confidence=0.92,
                reference_text=default_text,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return assets


class VoiceCatalogService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.catalog_root = self.workspace_root / "outputs" / "voice_catalog"
        self.audio_root = self.catalog_root / "audio"
        self.state_path = self.catalog_root / "catalog.json"
        self.audio_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._assets: dict[str, VoiceCatalogAsset] = {}
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        with self._lock:
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                assets = [VoiceCatalogAsset.model_validate(item) for item in payload.get("assets", [])]
                if not assets:
                    raise ValueError("音色库为空")
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                assets = _seed_assets()
                self._assets = {asset.asset_id: asset for asset in assets}
                self._persist_locked()
                return
            self._assets = {asset.asset_id: asset for asset in assets}
            if int(payload.get("schema_version", 1)) < 2:
                seed_assets = {asset.asset_id: asset for asset in _seed_assets()}
                self._assets = {
                    asset_id: asset.model_copy(
                        update={
                            "voice_prompt": seed_assets[asset_id].voice_prompt,
                            "rationale": seed_assets[asset_id].rationale,
                            "updated_at": _now(),
                        }
                    ) if asset_id in seed_assets and DETAILED_VOICE_NOTES[asset_id] not in asset.voice_prompt else asset
                    for asset_id, asset in self._assets.items()
                }
                self._persist_locked()

    def _persist_locked(self) -> None:
        self.catalog_root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"schema_version": 2, "assets": [asset.model_dump(mode="json") for asset in self._assets.values()]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _get_locked(self, asset_id: str) -> VoiceCatalogAsset:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise VoiceCatalogProblem(404, "音色资产不存在")
        return asset

    @staticmethod
    def presets() -> list[VoiceCatalogPreset]:
        return [preset.model_copy(deep=True) for preset in PRESETS]

    @staticmethod
    def reference_texts() -> list[VoiceCatalogReferenceText]:
        return [item.model_copy(deep=True) for item in REFERENCE_TEXTS]

    def payload(self) -> VoiceCatalogPayload:
        with self._lock:
            return VoiceCatalogPayload(
                presets=self.presets(),
                reference_texts=self.reference_texts(),
                assets=[asset.model_copy(deep=True) for asset in sorted(self._assets.values(), key=lambda item: item.display_name)],
            )

    def get_asset(self, asset_id: str) -> VoiceCatalogAsset:
        with self._lock:
            return self._get_locked(asset_id).model_copy(deep=True)

    def resolve_reference(self, asset_id: str, version_id: str | None = None) -> VoiceCatalogReference:
        """Resolve and validate one immutable catalog version for rendering."""
        with self._lock:
            asset = self._get_locked(asset_id)
            selected_id = version_id or asset.active_version_id
            version = next((item for item in asset.versions if item.version_id == selected_id), None)
            if version is None:
                raise VoiceCatalogProblem(404 if version_id else 409, "所选音色没有可用版本")
            if version.status != "complete" or not version.audio_url:
                raise VoiceCatalogProblem(409, "所选音色版本尚未生成完成")
            return VoiceCatalogReference(
                asset_id=asset.asset_id,
                display_name=asset.display_name,
                version_id=version.version_id,
                audio_url=version.audio_url,
                duration_seconds=version.duration_seconds,
                reference_text=asset.reference_text,
                voice_prompt=asset.voice_prompt,
            )

    def match_assets(
        self,
        *,
        gender: str | None = None,
        age_range: str | None = None,
        timbre_tags: list[str] | None = None,
        delivery_tags: list[str] | None = None,
        use_cases: list[str] | None = None,
        voice_prompt: str | None = None,
        limit: int = 8,
        require_ready: bool = False,
    ) -> list[VoiceCatalogMatch]:
        """Return deterministic, explainable catalog candidates.

        This is intentionally a lightweight ranking seam for workflow nodes.
        It does not mutate catalog state or silently bind a project snapshot;
        accepting a candidate remains an explicit product operation.
        """
        if not 1 <= limit <= 32:
            raise VoiceCatalogProblem(422, "声音匹配结果数量必须在 1 到 32 之间")

        def normalized(values: list[str] | str | None) -> set[str]:
            if isinstance(values, str):
                values = [values]
            return {item.strip().casefold() for item in (values or []) if item and item.strip()}

        requested_timbre = normalized(timbre_tags)
        requested_delivery = normalized(delivery_tags)
        requested_cases = normalized(use_cases)
        prompt = (voice_prompt or "").casefold()
        requested_gender = gender.strip().casefold() if isinstance(gender, str) and gender.strip() else None
        requested_age = age_range.strip().casefold() if isinstance(age_range, str) and age_range.strip() else None

        with self._lock:
            assets = [asset.model_copy(deep=True) for asset in self._assets.values()]

        ranked: list[VoiceCatalogMatch] = []
        for asset in assets:
            active = next((item for item in asset.versions if item.version_id == asset.active_version_id), None)
            ready = bool(active and active.status == "complete" and active.audio_url)
            if require_ready and not ready:
                continue

            score = 0.05
            reasons: list[str] = []
            if requested_gender:
                if asset.gender.casefold() == requested_gender:
                    score += 0.30
                    reasons.append("性别匹配")
                else:
                    score -= 0.12
            if requested_age:
                if asset.age_range.casefold() == requested_age:
                    score += 0.20
                    reasons.append("年龄段匹配")
                else:
                    score -= 0.05

            asset_timbre = normalized(asset.timbre_tags)
            asset_delivery = normalized(asset.delivery_tags)
            asset_cases = normalized(asset.use_cases)
            if requested_timbre:
                overlap = len(requested_timbre & asset_timbre)
                if overlap:
                    score += 0.25 * overlap / len(requested_timbre)
                    reasons.append(f"音色标签匹配 {overlap}/{len(requested_timbre)}")
            if requested_delivery:
                overlap = len(requested_delivery & asset_delivery)
                if overlap:
                    score += 0.15 * overlap / len(requested_delivery)
                    reasons.append(f"表达标签匹配 {overlap}/{len(requested_delivery)}")
            if requested_cases:
                overlap = len(requested_cases & asset_cases)
                if overlap:
                    score += 0.05 * overlap / len(requested_cases)
                    reasons.append(f"适用场景匹配 {overlap}/{len(requested_cases)}")
            if prompt:
                prompt_hits = sum(1 for tag in (*asset_timbre, *asset_delivery) if tag and tag in prompt)
                if prompt_hits:
                    score += min(0.05, prompt_hits * 0.01)
                    reasons.append("声线描述包含目录标签")
            if ready:
                reasons.append("已有已完成参考音频")
            else:
                reasons.append("尚无已完成参考音频")

            ranked.append(
                VoiceCatalogMatch(
                    asset_id=asset.asset_id,
                    display_name=asset.display_name,
                    score=round(max(0.0, min(1.0, score)), 4),
                    reasons=reasons[:8],
                    version_id=active.version_id if active else None,
                    audio_url=active.audio_url if active else None,
                    duration_seconds=active.duration_seconds if active else None,
                    ready=ready,
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.display_name, item.asset_id))
        return ranked[:limit]

    def update_asset(self, asset_id: str, request: VoiceCatalogAssetUpdate) -> VoiceCatalogAsset:
        with self._lock:
            asset = self._get_locked(asset_id)
            changes = request.model_dump(exclude_unset=True)
            if "generation_preset_id" in changes and changes["generation_preset_id"] not in {item.preset_id for item in PRESETS}:
                raise VoiceCatalogProblem(422, "未知的音色库生成参数方案")
            updated = asset.model_copy(update={**changes, "updated_at": _now()})
            self._assets[asset_id] = updated
            self._persist_locked()
            return updated.model_copy(deep=True)

    def _preset(self, preset_id: str | None, asset: VoiceCatalogAsset) -> VoiceCatalogPreset:
        selected = preset_id or asset.generation_preset_id
        for preset in PRESETS:
            if preset.preset_id == selected:
                return preset
        raise VoiceCatalogProblem(422, "未知的音色库生成参数方案")

    def _append_version_locked(
        self,
        asset: VoiceCatalogAsset,
        *,
        source: CatalogVersionSource,
        variant_name: str,
        variant_description: str,
        parent_version_id: str | None,
        emotion: str | None,
        intensity: float | None,
    ) -> VoiceCatalogVersion:
        now = _now()
        version = VoiceCatalogVersion(
            version_id=f"vcv_{uuid.uuid4().hex[:12]}",
            source=source,
            parent_version_id=parent_version_id,
            variant_name=variant_name,
            variant_description=variant_description,
            emotion=emotion,
            intensity=intensity,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._assets[asset.asset_id] = asset.model_copy(update={"versions": [*asset.versions, version], "updated_at": now})
        self._persist_locked()
        return version

    def generate_reference(self, asset_id: str, request: VoiceCatalogGenerateRequest, jobs: JobService) -> VoiceCatalogAsset:
        with self._lock:
            asset = self._get_locked(asset_id)
            preset = self._preset(request.preset_id, asset)
            text = request.reference_text or asset.reference_text
            prompt = request.voice_prompt or asset.voice_prompt
            version = self._append_version_locked(
                asset,
                source="generated",
                variant_name="基础参考",
                variant_description="使用角色审核式结构化声线描述生成的质量优先参考音频。",
                parent_version_id=None,
                emotion=None,
                intensity=None,
            )
        try:
            job = jobs.submit(
                JobRequest(
                    kind="catalog_reference",
                    text=text,
                    voice_prompt=prompt,
                    catalog_asset_id=asset_id,
                    catalog_version_id=version.version_id,
                    voxcpm_cfg_value=preset.cfg_value,
                    voxcpm_inference_timesteps=preset.inference_timesteps,
                )
            )
        except (JobProblem, ValueError, RuntimeError) as exc:
            self._record_job_event(asset_id, version.version_id, "failed", None, str(exc), None)
            raise VoiceCatalogProblem(getattr(exc, "status_code", 409), str(exc)) from exc
        self._patch_version(asset_id, version.version_id, job_id=job.job_id)
        return self.get_asset(asset_id)

    def derive_reference(self, asset_id: str, request: VoiceCatalogDerivativeRequest, jobs: JobService) -> VoiceCatalogAsset:
        with self._lock:
            asset = self._get_locked(asset_id)
            parent = next((item for item in asset.versions if item.version_id == request.parent_version_id), None)
            if parent is None or parent.status != "complete" or not parent.audio_url:
                raise VoiceCatalogProblem(409, "只能从已完成且有音频的版本派生")
            preset = self._preset(request.preset_id, asset)
            text = request.reference_text or asset.reference_text
            prompt = f"{asset.voice_prompt} 保持核心身份不变，{request.description} 强度约为 {request.intensity:.2f}。"
            version = self._append_version_locked(
                asset,
                source="derived",
                variant_name=request.variant_name,
                variant_description=request.description,
                parent_version_id=request.parent_version_id,
                emotion=request.emotion,
                intensity=request.intensity,
            )
        try:
            job = jobs.submit(
                JobRequest(
                    kind="catalog_derivative",
                    text=text,
                    voice_prompt=prompt,
                    catalog_asset_id=asset_id,
                    catalog_version_id=version.version_id,
                    voxcpm_cfg_value=preset.cfg_value,
                    voxcpm_inference_timesteps=preset.inference_timesteps,
                )
            )
        except (JobProblem, ValueError, RuntimeError) as exc:
            self._record_job_event(asset_id, version.version_id, "failed", None, str(exc), None)
            raise VoiceCatalogProblem(getattr(exc, "status_code", 409), str(exc)) from exc
        self._patch_version(asset_id, version.version_id, job_id=job.job_id)
        return self.get_asset(asset_id)

    def _patch_version(self, asset_id: str, version_id: str, **changes: object) -> None:
        with self._lock:
            asset = self._get_locked(asset_id)
            versions = [
                item.model_copy(update={**changes, "updated_at": _now()}) if item.version_id == version_id else item
                for item in asset.versions
            ]
            self._assets[asset_id] = asset.model_copy(update={"versions": versions, "updated_at": _now()})
            self._persist_locked()

    def record_job_event(
        self,
        asset_id: str,
        version_id: str,
        job_id: str,
        event_status: JobStatus,
        output_url: str | None,
        error: str | None,
    ) -> None:
        self._record_job_event(asset_id, version_id, event_status, output_url, error, job_id)

    def _record_job_event(
        self,
        asset_id: str,
        version_id: str,
        event_status: JobStatus,
        output_url: str | None,
        error: str | None,
        job_id: str | None,
    ) -> None:
        if event_status not in {"queued", "running", "complete", "failed"}:
            return
        with self._lock:
            asset = self._assets.get(asset_id)
            if asset is None:
                return
            version = next((item for item in asset.versions if item.version_id == version_id), None)
            if version is None:
                return
            changes: dict[str, object] = {"status": event_status, "updated_at": _now()}
            if job_id:
                changes["job_id"] = job_id
            if error:
                changes["error"] = error
            if event_status == "complete" and output_url:
                catalog_url, duration = self._archive_audio(asset_id, version_id, job_id, output_url)
                changes["audio_url"] = catalog_url
                changes["duration_seconds"] = duration
                changes["error"] = None
                active_version_id = version_id
            else:
                active_version_id = asset.active_version_id
            versions = [item.model_copy(update=changes) if item.version_id == version_id else item for item in asset.versions]
            self._assets[asset_id] = asset.model_copy(update={"versions": versions, "active_version_id": active_version_id, "updated_at": _now()})
            self._persist_locked()

    def _archive_audio(self, asset_id: str, version_id: str, job_id: str | None, output_url: str) -> tuple[str, float | None]:
        target = self.audio_root / asset_id / f"{version_id}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.workspace_root / "outputs" / "audio" / "jobs" / f"{job_id}.wav" if job_id else None
        if source is not None and source.is_file():
            shutil.copy2(source, target)
            return f"/media/outputs/voice-catalog/audio/{asset_id}/{version_id}.wav", self._duration(target)
        return output_url, None

    @staticmethod
    def _duration(path: Path) -> float | None:
        try:
            with wave.open(str(path), "rb") as audio:
                return round(audio.getnframes() / audio.getframerate(), 2)
        except (OSError, wave.Error, ZeroDivisionError):
            return None

    def activate_version(self, asset_id: str, version_id: str) -> VoiceCatalogAsset:
        with self._lock:
            asset = self._get_locked(asset_id)
            version = next((item for item in asset.versions if item.version_id == version_id), None)
            if version is None:
                raise VoiceCatalogProblem(404, "音色版本不存在")
            if version.status != "complete" or not version.audio_url:
                raise VoiceCatalogProblem(409, "只能激活已完成且有音频的版本")
            updated = asset.model_copy(update={"active_version_id": version_id, "updated_at": _now()})
            self._assets[asset_id] = updated
            self._persist_locked()
            return updated.model_copy(deep=True)

    def delete_version(self, asset_id: str, version_id: str) -> VoiceCatalogAsset:
        with self._lock:
            asset = self._get_locked(asset_id)
            version = next((item for item in asset.versions if item.version_id == version_id), None)
            if version is None:
                raise VoiceCatalogProblem(404, "音色版本不存在")
            if version_id == asset.active_version_id:
                raise VoiceCatalogProblem(409, "不能删除当前激活版本，请先切换到其他版本")
            remaining = [item for item in asset.versions if item.version_id != version_id]
            replacement = next((item for item in reversed(remaining) if item.status == "complete" and item.audio_url), None)
            updated = asset.model_copy(update={"versions": remaining, "active_version_id": replacement.version_id if replacement else None, "updated_at": _now()})
            self._assets[asset_id] = updated
            target = self.audio_root / asset_id / f"{version_id}.wav"
            target.unlink(missing_ok=True)
            self._persist_locked()
            return updated.model_copy(deep=True)


def _raise_http(error: VoiceCatalogProblem | JobProblem) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


def create_voice_catalog_router(catalog: VoiceCatalogService, jobs: JobService) -> APIRouter:
    router = APIRouter(prefix="/api/voice-catalog", tags=["voice-catalog"])

    @router.get("", response_model=VoiceCatalogPayload)
    def list_catalog() -> VoiceCatalogPayload:
        return catalog.payload()

    @router.get("/assets/{asset_id}", response_model=VoiceCatalogAsset)
    def get_asset(asset_id: str) -> VoiceCatalogAsset:
        try:
            return catalog.get_asset(asset_id)
        except VoiceCatalogProblem as exc:
            raise _raise_http(exc) from exc

    @router.post("/match", response_model=list[VoiceCatalogMatch])
    def match_assets(request: VoiceCatalogMatchRequest) -> list[VoiceCatalogMatch]:
        try:
            return catalog.match_assets(**request.model_dump())
        except VoiceCatalogProblem as exc:
            raise _raise_http(exc) from exc

    @router.patch("/assets/{asset_id}", response_model=VoiceCatalogAsset)
    def update_asset(asset_id: str, request: VoiceCatalogAssetUpdate) -> VoiceCatalogAsset:
        try:
            return catalog.update_asset(asset_id, request)
        except VoiceCatalogProblem as exc:
            raise _raise_http(exc) from exc

    @router.post("/assets/{asset_id}/generate", response_model=VoiceCatalogAsset, status_code=status.HTTP_202_ACCEPTED)
    def generate_reference(asset_id: str, request: VoiceCatalogGenerateRequest) -> VoiceCatalogAsset:
        try:
            return catalog.generate_reference(asset_id, request, jobs)
        except (VoiceCatalogProblem, JobProblem) as exc:
            raise _raise_http(exc) from exc

    @router.post("/assets/{asset_id}/derive", response_model=VoiceCatalogAsset, status_code=status.HTTP_202_ACCEPTED)
    def derive_reference(asset_id: str, request: VoiceCatalogDerivativeRequest) -> VoiceCatalogAsset:
        try:
            return catalog.derive_reference(asset_id, request, jobs)
        except (VoiceCatalogProblem, JobProblem) as exc:
            raise _raise_http(exc) from exc

    @router.post("/assets/{asset_id}/versions/{version_id}/activate", response_model=VoiceCatalogAsset)
    def activate_version(asset_id: str, version_id: str) -> VoiceCatalogAsset:
        try:
            return catalog.activate_version(asset_id, version_id)
        except VoiceCatalogProblem as exc:
            raise _raise_http(exc) from exc

    @router.delete("/assets/{asset_id}/versions/{version_id}", response_model=VoiceCatalogAsset)
    def delete_version(asset_id: str, version_id: str) -> VoiceCatalogAsset:
        try:
            return catalog.delete_version(asset_id, version_id)
        except VoiceCatalogProblem as exc:
            raise _raise_http(exc) from exc

    return router
