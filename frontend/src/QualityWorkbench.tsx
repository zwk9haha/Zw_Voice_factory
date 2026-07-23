import { Check, Download, FileAudio, ListMusic, LoaderCircle, Pause, Play, Plus, RefreshCw, Sparkles, Users, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createAudioJob, fetchAudioJob } from "./api";
import type { AudioJob, CharacterSummary, DirectorSegment, WorkspacePayload } from "./types";
import { Waveform } from "./Waveform";

interface QualityWorkbenchProps {
  workspace: WorkspacePayload;
}

const STANDARD_REFERENCE_TEXT = "雨后的长街渐渐安静下来，远处的钟声穿过薄雾，今天的故事也由此开始。";

function playbackFailureMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "浏览器未授予播放权限，请再次点击播放";
    if (error.name === "NotSupportedError") return "浏览器不支持当前音频格式";
    if (error.name === "AbortError") return "播放被新的音频操作中断";
    return `播放失败 · ${error.name}`;
  }
  return "播放失败，请检查音频服务";
}

function isPending(job: AudioJob | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function jobLabel(job: AudioJob | undefined, fallback: string): string {
  if (!job) return fallback;
  if (job.status === "failed") return `失败 · ${job.error ?? job.message}`;
  if (job.status === "complete") return "已生成";
  return `${job.progress}% · ${job.message}`;
}

export function QualityWorkbench({ workspace }: QualityWorkbenchProps) {
  const [activeCharacter, setActiveCharacter] = useState("xiao_yan");
  const [playing, setPlaying] = useState<string | null>(null);
  const [audioFeedback, setAudioFeedback] = useState("参考试听素材");
  const [operationFeedback, setOperationFeedback] = useState("GPU 任务等待操作");
  const [jobs, setJobs] = useState<Record<string, AudioJob>>({});
  const [referenceJobs, setReferenceJobs] = useState<Record<string, string>>({});
  const [segmentJobs, setSegmentJobs] = useState<Record<string, string>>({});
  const [submittingBatch, setSubmittingBatch] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const active = workspace.characters.find((item) => item.character_id === activeCharacter) ?? workspace.characters[0];
  const pendingJobIds = useMemo(
    () => Object.values(jobs).filter((job) => isPending(job)).map((job) => job.job_id).sort().join(","),
    [jobs],
  );

  useEffect(() => () => audioRef.current?.pause(), []);

  useEffect(() => {
    if (!pendingJobIds) return;
    const ids = pendingJobIds.split(",");
    let disposed = false;
    const poll = async () => {
      try {
        const refreshed = await Promise.all(ids.map(fetchAudioJob));
        if (disposed) return;
        setJobs((current) => {
          const next = { ...current };
          refreshed.forEach((job) => { next[job.job_id] = job; });
          return next;
        });
        const latest = refreshed.find((job) => job.status === "failed") ?? refreshed.at(-1);
        if (latest) setOperationFeedback(jobLabel(latest, latest.message));
      } catch (error) {
        if (!disposed) setOperationFeedback(error instanceof Error ? error.message : "任务状态同步失败");
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 1_000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [pendingJobIds]);

  function characterReference(character: CharacterSummary): string | null {
    const job = jobs[referenceJobs[character.character_id]];
    return job?.status === "complete" && job.output_url ? job.output_url : character.preview_audio_url;
  }

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
      setAudioFeedback("已暂停");
      return;
    }
    audio.pause();
    audio.src = audioUrl;
    audio.currentTime = 0;
    setPlaying(audioId);
    setAudioFeedback("加载中");
    audio.play().then(() => setAudioFeedback("播放中")).catch((error: unknown) => {
      setPlaying(null);
      setAudioFeedback(playbackFailureMessage(error));
    });
  }

  async function submitReference() {
    setOperationFeedback(`正在提交 ${active.display_name} 的 VoxCPM2 参考任务`);
    try {
      const job = await createAudioJob({
        kind: "voxcpm_reference",
        text: STANDARD_REFERENCE_TEXT,
        voice_prompt: active.voice_prompt,
        character_id: active.character_id,
      });
      setJobs((current) => ({ ...current, [job.job_id]: job }));
      setReferenceJobs((current) => ({ ...current, [active.character_id]: job.job_id }));
      setOperationFeedback(job.message);
    } catch (error) {
      setOperationFeedback(error instanceof Error ? error.message : "VoxCPM2 任务提交失败");
    }
  }

  async function submitSegment(segment: DirectorSegment) {
    const character = workspace.characters.find((item) => item.character_id === segment.character_id) ?? workspace.characters[0];
    const referenceAudioUrl = characterReference(character);
    if (!referenceAudioUrl) throw new Error(`${character.display_name} 尚无可用参考音频`);
    const job = await createAudioJob({
      kind: "quality_render",
      text: segment.text,
      character_id: character.character_id,
      segment_id: segment.segment_id,
      reference_audio_url: referenceAudioUrl,
    });
    setJobs((current) => ({ ...current, [job.job_id]: job }));
    setSegmentJobs((current) => ({ ...current, [segment.segment_id]: job.job_id }));
    return job;
  }

  async function regenerateSegment(segment: DirectorSegment) {
    setOperationFeedback(`正在提交 ${segment.speaker} 的 GPT-SoVITS 任务`);
    try {
      const job = await submitSegment(segment);
      setOperationFeedback(job.message);
    } catch (error) {
      setOperationFeedback(error instanceof Error ? error.message : "质量渲染任务提交失败");
    }
  }

  async function renderAll() {
    setSubmittingBatch(true);
    setOperationFeedback(`正在提交 ${workspace.segments.length} 个质量渲染任务`);
    try {
      const submitted = await Promise.all(workspace.segments.map(submitSegment));
      setOperationFeedback(`${submitted.length} 个任务已进入串行 GPU 队列`);
    } catch (error) {
      setOperationFeedback(error instanceof Error ? error.message : "批量任务提交失败");
    } finally {
      setSubmittingBatch(false);
    }
  }

  const activeReferenceJob = jobs[referenceJobs[active.character_id]];
  const activeReferenceUrl = characterReference(active);
  const activeReferenceAudioId = `reference:${active.character_id}`;
  const pendingCount = Object.values(jobs).filter((job) => isPending(job)).length;
  const failedCount = Object.values(jobs).filter((job) => job.status === "failed").length;
  const completedCount = Object.values(segmentJobs).filter((jobId) => jobs[jobId]?.status === "complete").length;

  return (
    <section className="workspace-grid">
      <audio ref={audioRef} className="audio-preview" aria-hidden="true" preload="metadata" onEnded={() => { setPlaying(null); setAudioFeedback("播放完成"); }} onError={() => { setPlaying(null); setAudioFeedback("音频加载失败"); }} />
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
          <div className="reference-toolbar"><span>标准参考 · {active.reference_backend === "voxcpm2" ? "VoxCPM2" : "IndexTTS2"}</span><button className="text-button" disabled={isPending(activeReferenceJob)} onClick={() => { void submitReference(); }}>{isPending(activeReferenceJob) ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}重新生成</button></div>
          {activeReferenceJob && <div className={`job-progress job-progress--${activeReferenceJob.status}`}><span style={{ width: `${activeReferenceJob.progress}%` }} /><small>{jobLabel(activeReferenceJob, "参考试听")}</small></div>}
          <Waveform color={active.color} />
          <div className="transport"><button className="icon-button" title={playing === activeReferenceAudioId ? "暂停标准参考" : "播放标准参考"} onClick={() => toggleAudio(activeReferenceAudioId, activeReferenceUrl)}>{playing === activeReferenceAudioId ? <Pause size={16} /> : <Play size={16} />}</button><span>WAV</span><span className="audio-feedback" role="status">{audioFeedback}</span><button className="accept-button"><Check size={14} />已采用</button></div>
          <div className="emotion-header"><span>情绪子体</span><button className="icon-button" title="生成情绪子体"><Sparkles size={15} /></button></div>
          <div className="emotion-list">
            {active.emotion_variants.map((emotion) => <button key={emotion} onClick={() => toggleAudio(`emotion:${active.character_id}:${emotion}`, activeReferenceUrl)}><Play size={12} /><span>{emotion}</span></button>)}
            {!active.emotion_variants.length && <span className="empty-inline">尚未生成</span>}
          </div>
        </section>
      </aside>

      <section className="script-pane">
        <div className="pane-heading script-heading"><div><span className="eyebrow">DIRECTOR SCORE</span><h2>导演脚本</h2></div><div className="script-tools"><span>{workspace.summary.segments} 句</span><button className="secondary-button"><Users size={15} />角色审核</button><button className="primary-button" disabled={submittingBatch} onClick={() => { void renderAll(); }}>{submittingBatch ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}全部生成</button></div></div>
        <div className="script-table-head"><span>角色 / 表演</span><span>文本</span><span>状态</span></div>
        <div className="script-list">
          {workspace.segments.map((segment, index) => {
            const character = workspace.characters.find((item) => item.character_id === segment.character_id) ?? workspace.characters[0];
            const job = jobs[segmentJobs[segment.segment_id]];
            const resultUrl = job?.status === "complete" && job.output_url ? job.output_url : null;
            return (
              <article className="script-row" key={segment.segment_id}>
                <div className="segment-meta"><span className={`cast-dot cast-dot--${character.color}`} /><strong>{segment.speaker}</strong><span className="emotion-chip">{segment.emotion}</span></div>
                <div className="segment-text"><span className="line-number">{String(index + 1).padStart(3, "0")}</span><p>{segment.text}</p></div>
                <button className="icon-button" disabled={!resultUrl} title={resultUrl ? (playing === `script:${segment.segment_id}` ? "暂停本句" : "播放本句") : "请先生成本句"} onClick={() => toggleAudio(`script:${segment.segment_id}`, resultUrl)}>{playing === `script:${segment.segment_id}` ? <Pause size={16} /> : <Play size={16} />}</button>
              </article>
            );
          })}
        </div>
        <footer className="timeline"><div><ListMusic size={16} /><span>GPU 任务队列</span><strong>{operationFeedback}</strong></div><div className="timeline-track"><span style={{ width: `${pendingCount ? 45 : completedCount ? 100 : 0}%` }} /></div><button className="primary-button" disabled={!completedCount}><Play size={15} />播放已完成</button></footer>
      </section>

      <aside className="result-pane">
        <div className="pane-heading"><div><span className="eyebrow">GPT-SOVITS</span><h2>生成结果</h2></div><span className={`queue-state ${failedCount ? "queue-state--failed" : ""}`}><span />{pendingCount ? `${pendingCount} 个任务进行中` : failedCount ? `${failedCount} 个任务失败` : "队列空闲"}</span></div>
        <div className="result-list">
          {workspace.segments.map((segment) => {
            const character = workspace.characters.find((item) => item.character_id === segment.character_id) ?? workspace.characters[0];
            const job = jobs[segmentJobs[segment.segment_id]];
            const resultUrl = job?.status === "complete" && job.output_url ? job.output_url : null;
            const resultAudioId = `result:${segment.segment_id}`;
            return (
              <article className="result-card" key={segment.segment_id}>
                <header><div><span className={`cast-dot cast-dot--${character.color}`} /><strong>{segment.speaker}</strong><span>{segment.emotion}</span></div><span className="render-time">{jobLabel(job, "待生成")}</span></header>
                <p>{segment.text}</p>
                {job && <div className={`job-progress job-progress--${job.status}`}><span style={{ width: `${job.progress}%` }} /></div>}
                <Waveform color={character.color} />
                <footer><button className="text-button" disabled={isPending(job)} onClick={() => { void regenerateSegment(segment); }}>{isPending(job) ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}重新生成</button><button className="text-button" onClick={() => toggleAudio(`emotion-result:${segment.segment_id}`, characterReference(character))}><Sparkles size={13} />情绪参考</button><span className="spacer" /><button className="icon-button" disabled={!resultUrl} title={resultUrl ? (playing === resultAudioId ? "暂停结果" : "播放结果") : "请先生成音频"} onClick={() => toggleAudio(resultAudioId, resultUrl)}>{playing === resultAudioId ? <Pause size={15} /> : <Play size={15} />}</button>{resultUrl ? <a className="icon-button" title="下载音频" href={resultUrl} download={`${segment.segment_id}.wav`}><Download size={15} /></a> : <button className="icon-button" disabled title="尚无音频"><Download size={15} /></button>}</footer>
              </article>
            );
          })}
        </div>
        <footer className="export-bar"><div><FileAudio size={16} /><span>已完成 {completedCount} / {workspace.segments.length}</span></div><button className="secondary-button" disabled={completedCount < 2}>合并音频</button></footer>
      </aside>
    </section>
  );
}
