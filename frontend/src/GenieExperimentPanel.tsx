import { AlertCircle, Clock3, Download, FlaskConical, LoaderCircle, X } from "lucide-react";
import { useEffect, useState } from "react";
import { AudioPlayer } from "./AudioPlayer";
import { compareGenieExperiment, fetchGenieVoices } from "./api";
import type { GenieCompareResult, GenieVoiceOption, QualityModelId, QualityRenderOptions } from "./types";
import { Waveform } from "./Waveform";

interface GenieExperimentReference {
  reference_id: string;
  display_name: string;
  reference_text: string;
  audio_url: string;
}

interface GenieExperimentPanelProps {
  projectId: string;
  references: GenieExperimentReference[];
  defaultReferenceId: string | null;
  defaultText: string;
  qualityModel: QualityModelId;
  qualityModelLabel: string;
  renderOptions: QualityRenderOptions;
  onClose: () => void;
}

function elapsedLabel(milliseconds: number): string {
  return `${(milliseconds / 1000).toFixed(1)} 秒`;
}

export function GenieExperimentPanel({
  projectId,
  references,
  defaultReferenceId,
  defaultText,
  qualityModel,
  qualityModelLabel,
  renderOptions,
  onClose,
}: GenieExperimentPanelProps) {
  const [voices, setVoices] = useState<GenieVoiceOption[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [referenceId, setReferenceId] = useState(defaultReferenceId ?? references[0]?.reference_id ?? "");
  const [text, setText] = useState(defaultText || "你好。");
  const [result, setResult] = useState<GenieCompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void fetchGenieVoices().then((nextVoices) => {
      if (!active) return;
      setVoices(nextVoices);
      setVoiceId((current) => current || nextVoices[0]?.voice_id || "");
    }).catch((nextError: unknown) => {
      if (active) setError(nextError instanceof Error ? nextError.message : "Genie 音色读取失败");
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (references.some((reference) => reference.reference_id === referenceId)) return;
    setReferenceId(references[0]?.reference_id ?? "");
  }, [referenceId, references]);

  const selectedReference = references.find((reference) => reference.reference_id === referenceId) ?? null;

  async function runExperiment() {
    if (!selectedReference || !voiceId || !text.trim()) {
      setError("请先选择可用参考音频、Genie 音色并输入测试文本");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await compareGenieExperiment({
        project_id: projectId,
        text: text.trim(),
        reference_audio_url: selectedReference.audio_url,
        reference_text: selectedReference.reference_text,
        voice_id: voiceId,
        character_name: `ab-${selectedReference.reference_id}`,
        language: "zh",
        gsv_model: qualityModel,
        render_options: renderOptions,
      }));
    } catch (nextError: unknown) {
      setError(nextError instanceof Error ? nextError.message : "A/B 实验失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="genie-ab-panel" aria-label="Genie-TTS A/B 实验">
      <header className="genie-ab-panel__header">
        <div><FlaskConical size={15} /><span><strong>Genie-TTS A/B 实验</strong><small>旁路试听，不改变正式质量渲染</small></span></div>
        <button className="icon-button" title="关闭 A/B 实验" onClick={onClose}><X size={15} /></button>
      </header>
      <div className="genie-ab-panel__controls">
        <label><span>参考音频</span><select value={referenceId} onChange={(event) => setReferenceId(event.target.value)}>{references.length ? references.map((reference) => <option key={reference.reference_id} value={reference.reference_id}>{reference.display_name}</option>) : <option value="">暂无可用参考音频</option>}</select></label>
        <label><span>Genie 音色</span><select value={voiceId} onChange={(event) => setVoiceId(event.target.value)}>{voices.length ? voices.map((voice) => <option key={voice.voice_id} value={voice.voice_id}>{voice.label} · {voice.voice_id}</option>) : <option value="">正在读取音色</option>}</select></label>
        <label className="genie-ab-panel__text"><span>试听文本</span><textarea value={text} maxLength={2000} onChange={(event) => setText(event.target.value)} /></label>
        <div className="genie-ab-panel__actions"><span>对照模型 · {qualityModelLabel}</span><button className="primary-button" disabled={busy || !selectedReference || !voiceId} onClick={() => void runExperiment()}>{busy ? <LoaderCircle className="spin" size={14} /> : <FlaskConical size={14} />}{busy ? "生成中，CPU 推理可能需要数十秒" : "开始 A/B 实验"}</button></div>
      </div>
      {error && <p className="genie-ab-panel__error"><AlertCircle size={14} />{error}</p>}
      {result && (
        <div className="genie-ab-results">
          <article className="genie-ab-result genie-ab-result--gsv">
            <header><div><strong>{qualityModelLabel}</strong><small>现有质量路线</small></div><span><Clock3 size={12} />{elapsedLabel(result.gsv_elapsed_ms)}</span></header>
            <p>{result.gsv_duration_seconds.toFixed(2)} 秒 · {result.gsv_elapsed_ms > 0 ? `${(result.gsv_elapsed_ms / Math.max(result.gsv_duration_seconds, 0.01)).toFixed(1)}x 实时` : "--"}</p>
            <Waveform src={result.gsv_audio_url} color="teal" />
            <AudioPlayer src={result.gsv_audio_url} label={`${qualityModelLabel} A/B 试听`} />
            <a className="text-button" href={result.gsv_audio_url} download={`${result.experiment_id}-quality.wav`}><Download size={13} />下载</a>
          </article>
          <article className="genie-ab-result genie-ab-result--genie">
            <header><div><strong>Genie-TTS</strong><small>ONNX CPU 试听引擎</small></div><span><Clock3 size={12} />{elapsedLabel(result.genie_elapsed_ms)}</span></header>
            <p>{result.genie_duration_seconds.toFixed(2)} 秒 · {result.genie_elapsed_ms > 0 ? `${(result.genie_elapsed_ms / Math.max(result.genie_duration_seconds, 0.01)).toFixed(1)}x 实时` : "--"}</p>
            <Waveform src={result.genie_audio_url} color="violet" />
            <AudioPlayer src={result.genie_audio_url} label="Genie-TTS A/B 试听" />
            <a className="text-button" href={result.genie_audio_url} download={`${result.experiment_id}-genie.wav`}><Download size={13} />下载</a>
          </article>
        </div>
      )}
    </section>
  );
}
