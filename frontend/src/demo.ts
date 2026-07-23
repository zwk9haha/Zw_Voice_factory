import type { WorkspacePayload } from "./types";

export const demoWorkspace: WorkspacePayload = {
  project: { id: "doupo_demo", name: "斗破苍穹", route: "quality" },
  summary: { characters: 3, accepted_references: 2, segments: 500, generated: 3 },
  workflow: [
    { stage_id: "template", label: "推理模板", status: "complete" },
    { stage_id: "source", label: "小说导入", status: "complete" },
    { stage_id: "casting", label: "角色审核", status: "complete" },
    { stage_id: "references", label: "标准参考", status: "complete" },
    { stage_id: "emotions", label: "情绪派生", status: "complete" },
    { stage_id: "director", label: "导演脚本", status: "complete" },
    { stage_id: "quality_render", label: "质量渲染", status: "current" },
  ],
  available_templates: [
    {
      template_id: "quality_character_consistency",
      display_name: "质量 · 角色一致性",
      analysis_profile: "balanced",
      segmentation_profile: "audiobook",
      reference_text_profile: "phoneme_coverage",
      quality_route: { reference_backend: "voxcpm2", render_backend: "gpt_sovits", stability_backend: "rvc", stability_policy: "benchmark_gated" },
    },
    {
      template_id: "quality_dialogue_dense",
      display_name: "质量 · 对话密集",
      analysis_profile: "character_recall",
      segmentation_profile: "dialogue_dense",
      reference_text_profile: "emotion_contrast",
      quality_route: { reference_backend: "voxcpm2", render_backend: "gpt_sovits", stability_backend: "rvc", stability_policy: "benchmark_gated" },
    },
    {
      template_id: "quality_long_form",
      display_name: "质量 · 长篇稳态",
      analysis_profile: "precision_first",
      segmentation_profile: "long_form",
      reference_text_profile: "phoneme_coverage",
      quality_route: { reference_backend: "voxcpm2", render_backend: "gpt_sovits", stability_backend: "rvc", stability_policy: "benchmark_gated" },
    },
  ],
  active_template: {
    template_id: "quality_character_consistency",
    display_name: "质量 · 角色一致性",
    analysis_profile: "balanced",
    segmentation_profile: "audiobook",
    reference_text_profile: "phoneme_coverage",
    quality_route: { reference_backend: "voxcpm2", render_backend: "gpt_sovits", stability_backend: "rvc", stability_policy: "benchmark_gated" },
  },
  characters: [
    { character_id: "narrator", display_name: "旁白", tier: "core", importance: 1, voice_prompt: "成熟、清晰、稳定的男声，叙述克制，具有空间感", reference_status: "accepted", reference_backend: "voxcpm2", preview_audio_url: "/media/voice-samples/curated/elder/male/voice_ref_34d05b99307a9c.wav", emotion_variants: ["自然", "庄重", "紧张"], color: "teal" },
    { character_id: "xiao_yan", display_name: "萧炎", tier: "core", importance: 0.94, voice_prompt: "青年男声，清亮但有韧劲，克制中保留爆发力", reference_status: "accepted", reference_backend: "voxcpm2", preview_audio_url: "/media/voice-samples/curated/young_adult/male/voice_ref_955e37aef1a1b7.wav", emotion_variants: ["自然", "愤怒", "悲伤"], color: "violet" },
    { character_id: "test_officer", display_name: "测验员", tier: "supporting", importance: 0.42, voice_prompt: "中年男声，冷淡、清晰、公式化", reference_status: "pending", reference_backend: "voxcpm2", preview_audio_url: "/media/voice-samples/curated/elder/male/voice_ref_0f3bba4cd9d384.wav", emotion_variants: [], color: "gold" },
  ],
  segments: [
    { segment_id: "s001", character_id: "narrator", speaker: "旁白", emotion: "紧张", text: "望着测验魔石碑上闪亮的五个大字，少年面无表情。" },
    { segment_id: "s002", character_id: "test_officer", speaker: "测验员", emotion: "冷淡", text: "萧炎，斗之力，三段。级别，低级。" },
    { segment_id: "s003", character_id: "xiao_yan", speaker: "萧炎", emotion: "克制", text: "三十年河东，三十年河西，莫欺少年穷。" },
  ],
};
