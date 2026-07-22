import { useState } from "react";
import {
  Activity,
  Check,
  ChevronDown,
  CircleStop,
  Download,
  FileAudio,
  Library,
  ListMusic,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Users,
  Volume2,
  WandSparkles,
} from "lucide-react";
import { demoWorkspace } from "./demo";
import type { CharacterSummary, RouteMode } from "./types";

const waveform = [18, 44, 32, 62, 28, 52, 70, 38, 24, 58, 42, 76, 35, 64, 48, 30, 68, 54, 22, 46, 72, 36, 56, 26, 60, 40, 66, 31, 50, 20, 74, 45];

function Waveform({ color }: { color: CharacterSummary["color"] }) {
  return (
    <div className={`waveform waveform--${color}`} aria-label="音频波形">
      {waveform.map((height, index) => (
        <span key={index} style={{ height: `${height}%` }} />
      ))}
    </div>
  );
}

function App() {
  const [mode, setMode] = useState<RouteMode>("quality");
  const [activeCharacter, setActiveCharacter] = useState("xiao_yan");
  const [playing, setPlaying] = useState<string | null>(null);
  const active = demoWorkspace.characters.find((item) => item.character_id === activeCharacter) ?? demoWorkspace.characters[0];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Volume2 size={18} /></div><strong>Zw Voice Factory</strong><span>工作台</span></div>
        <div className="project-switch"><Library size={15} /><span>斗破苍穹</span><ChevronDown size={14} /></div>
        <div className="route-switch" aria-label="朗读路线">
          <button className={mode === "fast" ? "active" : ""} onClick={() => setMode("fast")}>极速</button>
          <button className={mode === "quality" ? "active" : ""} onClick={() => setMode("quality")}>质量</button>
        </div>
        <div className="top-actions"><span className="health"><Activity size={14} />GPU 38%</span><button className="icon-button" title="保存项目"><Save size={17} /></button><button className="stop-button"><CircleStop size={15} />停止</button></div>
      </header>

      <section className="workspace-grid">
        <aside className="cast-pane">
          <div className="pane-heading"><div><span className="eyebrow">VOICE CAST</span><h2>角色声线</h2></div><button className="icon-button" title="添加角色"><Plus size={17} /></button></div>
          <div className="cast-list">
            {demoWorkspace.characters.map((character) => (
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
            <div className="reference-toolbar"><span>标准参考</span><button className="text-button"><RefreshCw size={14} />重新生成</button></div>
            <Waveform color={active.color} />
            <div className="transport"><button className="icon-button" title="播放标准参考" onClick={() => setPlaying(playing === "reference" ? null : "reference")}>{playing === "reference" ? <Pause size={16} /> : <Play size={16} />}</button><span>00:10</span><button className="accept-button"><Check size={14} />已采用</button></div>
            <div className="emotion-header"><span>情绪子体</span><button className="icon-button" title="生成情绪子体"><Sparkles size={15} /></button></div>
            <div className="emotion-list">
              {active.emotion_variants.map((emotion) => <button key={emotion}><Play size={12} /><span>{emotion}</span></button>)}
              {!active.emotion_variants.length && <span className="empty-inline">尚未生成</span>}
            </div>
          </section>
        </aside>

        <section className="script-pane">
          <div className="pane-heading script-heading"><div><span className="eyebrow">DIRECTOR SCORE</span><h2>导演脚本</h2></div><div className="script-tools"><span>500 句</span><button className="secondary-button"><Users size={15} />角色审核</button><button className="primary-button"><Sparkles size={15} />全部生成</button></div></div>
          <div className="script-table-head"><span>角色 / 表演</span><span>文本</span><span>状态</span></div>
          <div className="script-list">
            {demoWorkspace.segments.map((segment, index) => {
              const character = demoWorkspace.characters.find((item) => item.character_id === segment.character_id) ?? demoWorkspace.characters[0];
              return (
                <article className="script-row" key={segment.segment_id}>
                  <div className="segment-meta"><span className={`cast-dot cast-dot--${character.color}`} /><strong>{segment.speaker}</strong><span className="emotion-chip">{segment.emotion}</span></div>
                  <div className="segment-text"><span className="line-number">{String(index + 1).padStart(3, "0")}</span><p>{segment.text}</p></div>
                  <button className="icon-button" title="生成本句"><Play size={16} /></button>
                </article>
              );
            })}
          </div>
          <footer className="timeline"><div><ListMusic size={16} /><span>连续朗读队列</span><strong>03 / 500</strong></div><div className="timeline-track"><span /></div><button className="primary-button"><Play size={15} />从本句播放</button></footer>
        </section>

        <aside className="result-pane">
          <div className="pane-heading"><div><span className="eyebrow">RENDER QUEUE</span><h2>生成结果</h2></div><span className="queue-state"><span />运行中</span></div>
          <div className="result-list">
            {demoWorkspace.segments.map((segment, index) => {
              const character = demoWorkspace.characters.find((item) => item.character_id === segment.character_id) ?? demoWorkspace.characters[0];
              return (
                <article className="result-card" key={segment.segment_id}>
                  <header><div><span className={`cast-dot cast-dot--${character.color}`} /><strong>{segment.speaker}</strong><span>{segment.emotion}</span></div><span className="render-time">{index === 2 ? "生成中" : `${(1.3 + index * 0.4).toFixed(1)}s`}</span></header>
                  <p>{segment.text}</p>
                  <Waveform color={character.color} />
                  <footer><button className="text-button"><RefreshCw size={13} />重新生成</button><button className="text-button"><Sparkles size={13} />情绪参考</button><span className="spacer" /><button className="icon-button" title="播放结果" onClick={() => setPlaying(playing === segment.segment_id ? null : segment.segment_id)}>{playing === segment.segment_id ? <Pause size={15} /> : <Play size={15} />}</button><button className="icon-button" title="下载音频"><Download size={15} /></button></footer>
                </article>
              );
            })}
          </div>
          <footer className="export-bar"><div><FileAudio size={16} /><span>已完成 2 / 3</span></div><button className="secondary-button">合并音频</button></footer>
        </aside>
      </section>
    </main>
  );
}

export default App;
