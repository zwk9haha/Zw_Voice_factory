import type { WorkspacePayload } from "./types";

export const demoWorkspace: WorkspacePayload = {
  project: { id: "doupo_demo", name: "斗破苍穹", route: "quality" },
  summary: { characters: 5, accepted_references: 2, segments: 500, generated: 3 },
  characters: [
    { character_id: "narrator", display_name: "旁白", tier: "core", importance: 1, voice_prompt: "成熟、清晰、稳定的男声，叙述克制，具有空间感", reference_status: "accepted", emotion_variants: ["自然", "庄重", "紧张"], color: "teal" },
    { character_id: "xiao_yan", display_name: "萧炎", tier: "core", importance: 0.94, voice_prompt: "青年男声，清亮但有韧劲，克制中保留爆发力", reference_status: "accepted", emotion_variants: ["自然", "愤怒", "悲伤"], color: "violet" },
    { character_id: "test_officer", display_name: "测验员", tier: "supporting", importance: 0.42, voice_prompt: "中年男声，冷淡、清晰、公式化", reference_status: "pending", emotion_variants: [], color: "gold" },
  ],
  segments: [
    { segment_id: "s001", character_id: "narrator", speaker: "旁白", emotion: "紧张", text: "望着测验魔石碑上闪亮的五个大字，少年面无表情。" },
    { segment_id: "s002", character_id: "test_officer", speaker: "测验员", emotion: "冷淡", text: "萧炎，斗之力，三段。级别，低级。" },
    { segment_id: "s003", character_id: "xiao_yan", speaker: "萧炎", emotion: "克制", text: "三十年河东，三十年河西，莫欺少年穷。" },
  ],
};
