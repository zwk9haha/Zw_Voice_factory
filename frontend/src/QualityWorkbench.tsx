import { Check, Download, FileAudio, ListMusic, Pause, Play, Plus, RefreshCw, Sparkles, Users, WandSparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { WorkspacePayload } from "./types";
import { Waveform } from "./Waveform";

interface QualityWorkbenchProps {
  workspace: WorkspacePayload;
}

function playbackFailureMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "浏览器未授予播放权限，请使用播放器控件启动";
    if (error.name === "NotSupportedError") return "浏览器不支持当前音频格式";
    if (error.name === "AbortError") return "播放被新的音频操作中断";
    return `播放失败 · ${error.name}`;
  }
  return "播放失败，请检查音频服务";
}

export function QualityWorkbench({ workspace }: QualityWorkbenchProps) {
  const [activeCharacter, setActiveCharacter] = useState("xiao_yan");
  const [playing, setPlaying] = useState<string | null>(null);
  const [audioFeedback, setAudioFeedback] = useState("参考试听素材");
  const audioRef = useRef<HTMLAudioElement>(null);
  const active = workspace.characters.find((item) => item.character_id === activeCharacter) ?? workspace.characters[0];

  useEffect(() => {
    return () => audioRef.current?.pause();
  }, []);

  function toggleAudio(audioId: string, audioUrl: string | null) {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audioUrl) {
      setPlaying(null);
      setAudioFeedback("暂无可播放音频");
      return;
    }
    if (playing === audioId && !audio.paused) {
      audio.pause();
      setPlaying(null);
      setAudioFeedback("已暂停 · 参考试听素材");
      return;
    }
    audio.pause();
    audio.src = audioUrl;
    audio.currentTime = 0;
    setPlaying(audioId);
    setAudioFeedback("加载中 · 参考试听素材");
    audio.play().then(() => setAudioFeedback("播放中 · 参考试听素材")).catch((error: unknown) => {
      setPlaying(null);
      setAudioFeedback(playbackFailureMessage(error));
    });
  }

  return (
    <section className="workspace-grid">
      <audio ref={audioRef} className="audio-preview" aria-hidden="true" preload="metadata" onEnded={() => { setPlaying(null); setAudioFeedback("播放完成 · 参考试听素材"); }} onError={() => { setPlaying(null); setAudioFeedback("试听音频加载失败"); }} />
      <aside className="cast-pane">
        <div className="pane-heading">
          <div><span className="eyebrow">VOICE CAST</span><h2>角色声线</h2></div>
          <button className="icon-button" title="添加角色"><Plus size={17} /></button>
        </div>
        <div className="cast-list">
          {workspace.characters.map((character) => (
            <button key={character.character_id} className={`cast-item ${activeCharacter === character.character_id ? "selected" : ""}`} onClick={() => setActiveCharacter(character.character_id)}>
              <span className={`cast-dot cast-dot--${character.color}`} />
              <span className="cast-copy"><strong>{character.display_name}</strong><small>{character.tier === "core" ? "核心角色" : "配角"} · 权重 {Math.round(character.importance * 100)}</small></span>
              <span className={`status status--${character.reference_status}`}>{character.reference_status === "accepted" ? "已确认" : "待审核"}</span>
            </button>
          ))}
        </div>

        <section className="voice-editor">
          <div className="section-title"><WandSparkles size={16} /><h3>{active.display_name} · 声线设计</h3></div>
          <textarea value={active.voice_prompt} readOnly aria-label="声线描述" />
          <div className="reference-toolbar"><span>标准参考 · {active.reference_backend === "voxcpm2" ? "VoxCPM2" : "IndexTTS2"}</span><button className="text-button"><RefreshCw size={14} />重新生成</button></div>
          <Waveform color={active.color} />
          <div className="transport"><button className="icon-button" title={playing === "reference" ? "暂停标准参考" : "播放标准参考"} onClick={() => toggleAudio("reference", active.preview_audio_url)}>{playing === "reference" ? <Pause size={16} /> : <Play size={16} />}</button><span>00:10</span><span className="audio-feedback" role="status">{audioFeedback}</span><button className="accept-button"><Check size={14} />已采用</button></div>
          <div className="emotion-header"><span>情绪子体</span><button className="icon-button" title="生成情绪子体"><Sparkles size={15} /></button></div>
          <div className="emotion-list">
            {active.emotion_variants.map((emotion) => <button key={emotion} onClick={() => toggleAudio(`emotion:${active.character_id}:${emotion}`, active.preview_audio_url)}><Play size={12} /><span>{emotion}</span></button>)}
            {!active.emotion_variants.length && <span className="empty-inline">尚未生成</span>}
          </div>
        </section>
      </aside>

      <section className="script-pane">
        <div className="pane-heading script-heading"><div><span className="eyebrow">DIRECTOR SCORE</span><h2>导演脚本</h2></div><div className="script-tools"><span>{workspace.summary.segments} 句</span><button className="secondary-button"><Users size={15} />角色审核</button><button className="primary-button"><Sparkles size={15} />全部生成</button></div></div>
        <div className="script-table-head"><span>角色 / 表演</span><span>文本</span><span>状态</span></div>
        <div className="script-list">
          {workspace.segments.map((segment, index) => {
            const character = workspace.characters.find((item) => item.character_id === segment.character_id) ?? workspace.characters[0];
            return (
              <article className="script-row" key={segment.segment_id}>
                <div className="segment-meta"><span className={`cast-dot cast-dot--${character.color}`} /><strong>{segment.speaker}</strong><span className="emotion-chip">{segment.emotion}</span></div>
                <div className="segment-text"><span className="line-number">{String(index + 1).padStart(3, "0")}</span><p>{segment.text}</p></div>
                <button className="icon-button" title={playing === segment.segment_id ? "暂停本句" : "播放本句"} onClick={() => toggleAudio(segment.segment_id, character.preview_audio_url)}>{playing === segment.segment_id ? <Pause size={16} /> : <Play size={16} />}</button>
              </article>
            );
          })}
        </div>
        <footer className="timeline"><div><ListMusic size={16} /><span>连续朗读队列</span><strong>03 / {workspace.summary.segments}</strong></div><div className="timeline-track"><span /></div><button className="primary-button"><Play size={15} />从本句播放</button></footer>
      </section>

      <aside className="result-pane">
        <div className="pane-heading"><div><span className="eyebrow">GPT-SOVITS</span><h2>生成结果</h2></div><span className="queue-state"><span />运行中</span></div>
        <div className="result-list">
          {workspace.segments.map((segment, index) => {
            const character = workspace.characters.find((item) => item.character_id === segment.character_id) ?? workspace.characters[0];
            return (
              <article className="result-card" key={segment.segment_id}>
                <header><div><span className={`cast-dot cast-dot--${character.color}`} /><strong>{segment.speaker}</strong><span>{segment.emotion}</span></div><span className="render-time">{index === 2 ? "待生成" : "参考试听"}</span></header>
                <p>{segment.text}</p>
                <Waveform color={character.color} />
                <footer><button className="text-button"><RefreshCw size={13} />重新生成</button><button className="text-button" onClick={() => toggleAudio(`emotion-result:${segment.segment_id}`, character.preview_audio_url)}><Sparkles size={13} />情绪参考</button><span className="spacer" /><button className="icon-button" title={playing === segment.segment_id ? "暂停结果" : "播放结果"} onClick={() => toggleAudio(segment.segment_id, character.preview_audio_url)}>{playing === segment.segment_id ? <Pause size={15} /> : <Play size={15} />}</button><button className="icon-button" title="下载音频"><Download size={15} /></button></footer>
              </article>
            );
          })}
        </div>
        <footer className="export-bar"><div><FileAudio size={16} /><span>已完成 2 / 3</span></div><button className="secondary-button">合并音频</button></footer>
      </aside>
    </section>
  );
}
