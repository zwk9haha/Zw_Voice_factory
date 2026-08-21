import { Library, LoaderCircle, RefreshCw, RotateCcw } from "lucide-react";
import { AudioPlayer } from "./AudioPlayer";
import type { VoiceResourceMatch } from "./types";

interface VoiceReusePanelProps {
  matches: VoiceResourceMatch[];
  disabled: boolean;
  loading: boolean;
  onRefresh: () => Promise<void>;
  onReuse: (match: VoiceResourceMatch) => Promise<void>;
}

const genderLabel = { male: "男声", female: "女声", unknown: "性别待定" } as const;

export function VoiceReusePanel({ matches, disabled, loading, onRefresh, onReuse }: VoiceReusePanelProps) {
  return (
    <section className="voice-reuse-panel" aria-label="历史声线资源复用">
      <header>
        <div><Library size={14} /><span>历史声线匹配</span><strong>{matches.length ? `${matches.length} 个候选` : "暂无匹配"}</strong></div>
        <button className="icon-button" title="重新匹配历史声线" disabled={disabled || loading} onClick={() => void onRefresh()}>{loading ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}</button>
      </header>
      {matches.length ? (
        <div className="voice-reuse-list">
          {matches.map((match) => (
            <article key={`${match.source_project_id}:${match.source_reference_id}:${match.source_version_id}`} className="voice-reuse-item">
              <div className="voice-reuse-meta">
                <span><strong>{match.display_name}</strong><em>{Math.round(match.similarity * 100)}% 相近</em></span>
                <small>{match.source_project_name} · {genderLabel[match.gender]}</small>
                <p>{match.voice_prompt}</p>
              </div>
              <AudioPlayer className="voice-reuse-audio" src={match.audio_url} label={`${match.source_project_name} · ${match.display_name}历史声线试听`} />
              <button className="secondary-button" disabled={disabled} onClick={() => void onReuse(match)}><RotateCcw size={14} />复用此参考</button>
            </article>
          ))}
        </div>
      ) : !loading && <p className="voice-reuse-empty">其他项目还没有可匹配的参考音频。</p>}
    </section>
  );
}
