import type { AudioJob, PreparedCharacter, PreparedDirectorSegment, QualityModelId, ReferencePlanItem } from "./types";

type VoiceGender = "male" | "female" | "unknown";

export type QualityVoiceReason = "manual" | "global_narrator" | "character" | "gender_narrator" | "extra_random" | "opposite_narrator";

export interface QualityVoiceDecision {
  referenceId: string | null;
  reason: QualityVoiceReason;
}

interface ResolveQualityVoiceOptions<TReference extends ReferencePlanItem> {
  segment: PreparedDirectorSegment;
  references: TReference[];
  characters: PreparedCharacter[];
  narratorGender: "male" | "female";
  contextText?: string;
  isPlayableReference: (reference: TReference) => boolean;
  hasDistinctAudio: (reference: TReference) => boolean;
}

const FEMALE_SPEAKER_CLUES = ["本小姐", "本姑娘", "小女子", "奴家", "妾身", "老娘", "姐姐我", "女声"];
const MALE_SPEAKER_CLUES = ["本少爷", "本公子", "老子", "老夫", "小爷", "大爷我", "哥哥我", "男声"];
const FEMALE_IDENTITY_CLUES = ["女子", "少女", "小姐", "姑娘", "夫人", "女士", "母亲", "姐姐", "妹妹", "侍女", "老妇"];
const MALE_IDENTITY_CLUES = ["男子", "少年", "先生", "公子", "少爷", "父亲", "哥哥", "弟弟", "老者", "大汉", "侍卫"];
const EXTRA_IDENTITY_CLUES = ["路人", "龙套", "守卫", "门卫", "侍卫", "侍女", "侍从", "护卫", "伙计", "店员", "摊主", "客人", "观众", "群众", "众人", "人群", "佣兵", "士兵", "弟子", "学员", "陌生人", "某人", "声音"];
const EXTRA_DIALOGUE_CLUES = ["人群中有人", "一道声音", "有人喊", "有人说道", "有人开口", "众人喊", "众人说道", "周围有人"];

function clueScore(value: string, clues: string[]): number {
  return clues.reduce((score, clue) => score + (value.includes(clue) ? 1 : 0), 0);
}

function inferSpeakerGender(character: PreparedCharacter | undefined, segment: PreparedDirectorSegment, references: ReferencePlanItem[], contextText: string): VoiceGender {
  if (segment.speaker_gender === "male" || segment.speaker_gender === "female") return segment.speaker_gender;
  if (character?.gender === "male" || character?.gender === "female") return character.gender;
  const referenceGender = references.find((reference) => reference.gender !== "unknown")?.gender;
  if (referenceGender === "male" || referenceGender === "female") return referenceGender;

  const identity = [character?.display_name ?? "", ...(character?.aliases ?? [])].join(" ");
  const femaleScore = clueScore(identity, FEMALE_IDENTITY_CLUES) + clueScore(segment.text, FEMALE_SPEAKER_CLUES) + clueScore(contextText, FEMALE_IDENTITY_CLUES);
  const maleScore = clueScore(identity, MALE_IDENTITY_CLUES) + clueScore(segment.text, MALE_SPEAKER_CLUES) + clueScore(contextText, MALE_IDENTITY_CLUES);
  if (femaleScore > maleScore) return "female";
  if (maleScore > femaleScore) return "male";
  return "unknown";
}

function stableIndex(value: string, length: number): number {
  let hash = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return (hash >>> 0) % length;
}

function isObviousExtra(character: PreparedCharacter | undefined, segment: PreparedDirectorSegment, characterReferences: ReferencePlanItem[], contextText: string): boolean {
  if (segment.speaker_kind === "extra") return true;
  const identity = [character?.display_name ?? "", ...(character?.aliases ?? [])].join(" ");
  return character?.tier === "minor"
    || characterReferences.some((reference) => reference.selection_mode === "optional" && !reference.selected)
    || EXTRA_IDENTITY_CLUES.some((clue) => identity.includes(clue))
    || EXTRA_DIALOGUE_CLUES.some((clue) => contextText.includes(clue));
}

export function resolveQualityVoice<TReference extends ReferencePlanItem>({
  segment,
  references,
  characters,
  narratorGender,
  contextText = segment.text,
  isPlayableReference,
  hasDistinctAudio,
}: ResolveQualityVoiceOptions<TReference>): QualityVoiceDecision {
  const byId = new Map(references.map((reference) => [reference.reference_id, reference]));
  if (segment.voice_reference_id && byId.has(segment.voice_reference_id)) {
    return { referenceId: segment.voice_reference_id, reason: "manual" };
  }

  const narrator = (gender: "male" | "female") => references.find(
    (reference) => reference.selection_mode === "narrator_default" && reference.gender === gender,
  ) ?? null;
  if (segment.segment_type === "narration") {
    return { referenceId: narrator(narratorGender)?.reference_id ?? null, reason: "global_narrator" };
  }

  const character = characters.find((item) => item.character_id === segment.character_id);
  const characterReferences = references.filter(
    (reference) => reference.selection_mode !== "narrator_default" && reference.source_character_id === segment.character_id,
  );
  const selectedCharacterReference = characterReferences.find((reference) => reference.selected && isPlayableReference(reference))
    ?? characterReferences.find((reference) => reference.selected);
  if (selectedCharacterReference) {
    return { referenceId: selectedCharacterReference.reference_id, reason: "character" };
  }

  const gender = inferSpeakerGender(character, segment, characterReferences, contextText);
  if (gender === "male" || gender === "female") {
    return { referenceId: narrator(gender)?.reference_id ?? null, reason: "gender_narrator" };
  }

  if (isObviousExtra(character, segment, characterReferences, contextText)) {
    const randomCandidates = references
      .filter((reference) => reference.selection_mode !== "narrator_default" && reference.selected && hasDistinctAudio(reference))
      .sort((left, right) => left.reference_id.localeCompare(right.reference_id));
    if (randomCandidates.length) {
      const selected = randomCandidates[stableIndex(segment.segment_id, randomCandidates.length)];
      return { referenceId: selected.reference_id, reason: "extra_random" };
    }
  }

  const oppositeGender = narratorGender === "female" ? "male" : "female";
  return { referenceId: narrator(oppositeGender)?.reference_id ?? narrator(narratorGender)?.reference_id ?? null, reason: "opposite_narrator" };
}

interface CollectReusableJobsOptions {
  segments: PreparedDirectorSegment[];
  jobs: AudioJob[];
  qualityModel: QualityModelId;
  referenceIdForSegment: (segment: PreparedDirectorSegment) => string | null;
  referenceAudioUrlForSegment: (segment: PreparedDirectorSegment) => string | null;
  rvcModelIdForSegment: (segment: PreparedDirectorSegment) => string | null;
  rvcProfileFingerprintForSegment: (segment: PreparedDirectorSegment) => string | null;
}

export function collectReusableQualityJobs({ segments, jobs, qualityModel, referenceIdForSegment, referenceAudioUrlForSegment, rvcModelIdForSegment, rvcProfileFingerprintForSegment }: CollectReusableJobsOptions): Map<string, string> {
  const segmentIds = new Set(segments.map((segment) => segment.segment_id));
  const expectedReferences = new Map(segments.map((segment) => [segment.segment_id, referenceIdForSegment(segment)]));
  const expectedAudioUrls = new Map(segments.map((segment) => [segment.segment_id, referenceAudioUrlForSegment(segment)]));
  const expectedRvcModels = new Map(segments.map((segment) => [segment.segment_id, rvcModelIdForSegment(segment)]));
  const expectedRvcProfiles = new Map(segments.map((segment) => [segment.segment_id, rvcProfileFingerprintForSegment(segment)]));
  const reusable = new Map<string, AudioJob>();

  [...jobs]
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .forEach((job) => {
      if (job.kind !== "quality_render" || !job.segment_id || !segmentIds.has(job.segment_id) || reusable.has(job.segment_id)) return;
      if (job.status === "failed" || job.status === "cancelled") return;
      if (job.status === "complete" && !job.output_url) return;
      const expectedReference = expectedReferences.get(job.segment_id);
      if (!expectedReference || job.reference_id !== expectedReference) return;
      const expectedAudioUrl = expectedAudioUrls.get(job.segment_id);
      if (!expectedAudioUrl || job.reference_audio_url !== expectedAudioUrl) return;
      if (job.quality_model && job.quality_model !== qualityModel) return;
      if ((job.rvc_model_id ?? null) !== (expectedRvcModels.get(job.segment_id) ?? null)) return;
      if ((job.rvc_profile_fingerprint ?? null) !== (expectedRvcProfiles.get(job.segment_id) ?? null)) return;
      if (job.rvc_status === "fallback") return;
      reusable.set(job.segment_id, job);
    });

  return new Map([...reusable].map(([segmentId, job]) => [segmentId, job.job_id]));
}
