import { RotateCcw, Save, SlidersHorizontal, X } from "lucide-react";
import type { ProgramLoudnessPolicy, QualityRenderOptions } from "./types";

export const DEFAULT_QUALITY_RENDER_OPTIONS: QualityRenderOptions = {
  chunk_length: 120,
  top_k: 30,
  top_p: 0.8,
  temperature: 0.8,
  repetition_penalty: 1.35,
  speed_factor: 1,
  fragment_interval: 0.3,
  batch_size: 1,
  split_bucket: true,
  seed: -1,
  emotion_strength: 0.75,
};

export const DEFAULT_PROGRAM_LOUDNESS_POLICY: ProgramLoudnessPolicy = {
  schema_version: 1,
  enabled: true,
  target_lufs: -18,
  true_peak_dbtp: -1,
  target_lra: 11,
  max_segment_gain_db: 4,
};

interface NumberSettingProps {
  label: string;
  name: keyof QualityRenderOptions;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (name: keyof QualityRenderOptions, value: number) => void;
}

function NumberSetting({ label, name, value, min, max, step, onChange }: NumberSettingProps) {
  const progress = ((value - min) / (max - min)) * 100;
  return (
    <label className="advanced-setting">
      <span><strong>{label}</strong><code>{name}</code></span>
      <input type="number" min={min} max={max} step={step} value={value} onChange={(event) => onChange(name, Number(event.target.value))} />
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        style={{ background: `linear-gradient(to right, var(--theme-accent) 0%, var(--theme-accent) ${progress}%, var(--theme-control-track) ${progress}%, var(--theme-control-track) 100%)` }}
        onChange={(event) => onChange(name, Number(event.target.value))}
      />
      <small><span>{min}</span><span>{max}</span></small>
    </label>
  );
}

interface LoudnessNumberSettingProps {
  label: string;
  name: keyof Omit<ProgramLoudnessPolicy, "schema_version" | "enabled">;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (name: keyof Omit<ProgramLoudnessPolicy, "schema_version" | "enabled">, value: number) => void;
}

function LoudnessNumberSetting({ label, name, value, min, max, step, onChange }: LoudnessNumberSettingProps) {
  const progress = ((value - min) / (max - min)) * 100;
  return (
    <label className="advanced-setting">
      <span><strong>{label}</strong><code>{name}</code></span>
      <input type="number" min={min} max={max} step={step} value={value} onChange={(event) => onChange(name, Number(event.target.value))} />
      <input type="range" min={min} max={max} step={step} value={value} style={{ background: `linear-gradient(to right, var(--theme-accent) 0%, var(--theme-accent) ${progress}%, var(--theme-control-track) ${progress}%, var(--theme-control-track) 100%)` }} onChange={(event) => onChange(name, Number(event.target.value))} />
      <small><span>{min}</span><span>{max}</span></small>
    </label>
  );
}

interface AdvancedSettingsPanelProps {
  options: QualityRenderOptions;
  loudness: ProgramLoudnessPolicy;
  renderer: "gpt_sovits" | "indextts2";
  saving: boolean;
  onChange: (options: QualityRenderOptions) => void;
  onLoudnessChange: (policy: ProgramLoudnessPolicy) => void;
  onSave: () => void;
  onClose: () => void;
}

export function AdvancedSettingsPanel({ options, loudness, renderer, saving, onChange, onLoudnessChange, onSave, onClose }: AdvancedSettingsPanelProps) {
  function change(name: keyof QualityRenderOptions, value: number) {
    onChange({ ...options, [name]: value });
  }

  function changeLoudness(name: keyof Omit<ProgramLoudnessPolicy, "schema_version" | "enabled">, value: number) {
    onLoudnessChange({ ...loudness, [name]: value });
  }

  return (
    <div className="advanced-settings-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="advanced-settings-panel" role="dialog" aria-modal="true" aria-label="质量渲染高级设置">
        <header>
          <div><SlidersHorizontal size={17} /><h2>高级设置</h2><span>{renderer === "gpt_sovits" ? "GPT-SoVITS" : "IndexTTS2"}</span></div>
          <button className="icon-button" title="关闭高级设置" onClick={onClose}><X size={16} /></button>
        </header>
        <div className="advanced-settings-scroll">
          <NumberSetting label="单段最大长度" name="chunk_length" value={options.chunk_length} min={20} max={300} step={10} onChange={change} />
          <NumberSetting label="Top-K" name="top_k" value={options.top_k} min={1} max={100} step={1} onChange={change} />
          <NumberSetting label="Top-P" name="top_p" value={options.top_p} min={0.1} max={1} step={0.01} onChange={change} />
          <NumberSetting label="温度" name="temperature" value={options.temperature} min={0.1} max={1.5} step={0.01} onChange={change} />
          <NumberSetting label="重复惩罚" name="repetition_penalty" value={options.repetition_penalty} min={1} max={renderer === "indextts2" ? 12 : 2} step={0.01} onChange={change} />
          <NumberSetting label="片段间隔" name="fragment_interval" value={options.fragment_interval} min={0.05} max={1} step={0.05} onChange={change} />
          {renderer === "gpt_sovits" && <NumberSetting label="语速" name="speed_factor" value={options.speed_factor} min={0.6} max={1.5} step={0.01} onChange={change} />}
          {renderer === "gpt_sovits" && <NumberSetting label="批大小" name="batch_size" value={options.batch_size} min={1} max={16} step={1} onChange={change} />}
          {renderer === "indextts2" && <NumberSetting label="情绪强度" name="emotion_strength" value={options.emotion_strength} min={0} max={1} step={0.01} onChange={change} />}
          <label className="advanced-setting advanced-setting--seed">
            <span><strong>随机种子</strong><code>seed</code></span>
            <input type="number" min="-1" max="2147483647" step="1" value={options.seed} onChange={(event) => change("seed", Number(event.target.value))} />
          </label>
          {renderer === "gpt_sovits" && (
            <label className="advanced-toggle">
              <span><strong>分桶并行</strong><code>split_bucket</code></span>
              <input type="checkbox" checked={options.split_bucket} onChange={(event) => onChange({ ...options, split_bucket: event.target.checked })} />
              <span className="toggle-track"><span /></span>
            </label>
          )}
          <label className="advanced-toggle">
            <span><strong>节目响度统一</strong><code>program_loudness</code></span>
            <input type="checkbox" checked={loudness.enabled} onChange={(event) => onLoudnessChange({ ...loudness, enabled: event.target.checked })} />
            <span className="toggle-track"><span /></span>
          </label>
          <LoudnessNumberSetting label="目标响度" name="target_lufs" value={loudness.target_lufs} min={-23} max={-14} step={0.5} onChange={changeLoudness} />
          <LoudnessNumberSetting label="真峰值上限" name="true_peak_dbtp" value={loudness.true_peak_dbtp} min={-3} max={-0.1} step={0.1} onChange={changeLoudness} />
          <LoudnessNumberSetting label="最大句间增益" name="max_segment_gain_db" value={loudness.max_segment_gain_db} min={1} max={8} step={0.5} onChange={changeLoudness} />
        </div>
        <footer>
          <button className="secondary-button" onClick={() => { onChange(DEFAULT_QUALITY_RENDER_OPTIONS); onLoudnessChange(DEFAULT_PROGRAM_LOUDNESS_POLICY); }}><RotateCcw size={14} />恢复默认</button>
          <button className="primary-button" disabled={saving} onClick={onSave}><Save size={14} />{saving ? "保存中" : "保存设置"}</button>
        </footer>
      </section>
    </div>
  );
}
