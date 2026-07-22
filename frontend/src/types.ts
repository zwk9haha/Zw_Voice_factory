export type RouteMode = "fast" | "quality";

export interface CharacterSummary {
  character_id: string;
  display_name: string;
  tier: "core" | "supporting" | "minor" | "uncertain";
  importance: number;
  voice_prompt: string;
  reference_status: "pending" | "accepted" | "rejected";
  emotion_variants: string[];
  color: "teal" | "violet" | "gold";
}

export interface DirectorSegment {
  segment_id: string;
  character_id: string;
  speaker: string;
  emotion: string;
  text: string;
}

export interface WorkspacePayload {
  project: { id: string; name: string; route: RouteMode };
  summary: { characters: number; accepted_references: number; segments: number; generated: number };
  characters: CharacterSummary[];
  segments: DirectorSegment[];
}
