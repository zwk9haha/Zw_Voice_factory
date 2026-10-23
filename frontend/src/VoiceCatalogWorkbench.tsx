import { Check, ChevronLeft, CircleAlert, Clock3, CopyPlus, FileAudio, Filter, LoaderCircle, Play, RefreshCw, Save, Sparkles, Trash2, Volume2, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { activateVoiceCatalogVersion, deleteVoiceCatalogVersion, deriveVoiceCatalogAsset, fetchVoiceCatalog, generateVoiceCatalogAsset, updateVoiceCatalogAsset } from "./api";
import { AudioPlayer } from "./AudioPlayer";
import { Waveform } from "./Waveform";
import type { CharacterSummary, VoiceCatalogAsset, VoiceCatalogPayload, VoiceCatalogPreset, VoiceCatalogVersion } from "./types";

interface VoiceCatalogWorkbenchProps {
  onBack: () => void;
}

const colorByGender: Record<VoiceCatalogAsset["gender"], CharacterSummary["color"]> = { female: "violet", male: "teal" };
const genderLabels: Record<VoiceCatalogAsset["gender"], string> = { female: "女声", male: "男声" };
const ageLabels: Record<VoiceCatalogAsset["age_range"], string> = { young_adult: "青年", adult: "成年", middle_aged: "中老年", senior: "老年" };

function formatDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function versionState(version: VoiceCatalogVersion): string {
  if (version.status === "queued") return "排队中";
  if (version.status === "running") return "生成中";
  if (version.status === "failed") return "生成失败";
  return version.duration_seconds ? `${version.duration_seconds.toFixed(1)} 秒` : "已完成";
}

export function VoiceCatalogWorkbench({ onBack }: VoiceCatalogWorkbenchProps) {
  const [catalog, setCatalog] = useState<VoiceCatalogPayload | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [gender, setGender] = useState<"all" | VoiceCatalogAsset["gender"]>("all");
  const [age, setAge] = useState<"all" | VoiceCatalogAsset["age_range"]>("all");
  const [search, setSearch] = useState("");
  const [presetId, setPresetId] = useState("quality_voice_catalog");
  const [referenceText, setReferenceText] = useState("");
  const [prompt, setPrompt] = useState("");
  const [variantName, setVariantName] = useState("轻柔表达");
  const [variantDescription, setVariantDescription] = useState("保持核心音色身份不变，语气更柔和，气息更松弛，句尾收束自然。");
  const [variantEmotion, setVariantEmotion] = useState("温柔");
  const [intensity, setIntensity] = useState(0.45);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  async function loadCatalog(): Promise<void> {
    try {
      const next = await fetchVoiceCatalog();
      setCatalog(next);
      setSelectedId((current) => current && next.assets.some((asset) => asset.asset_id === current) ? current : next.assets[0]?.asset_id ?? null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "音色库读取失败");
    }
  }

  useEffect(() => { void loadCatalog(); }, []);

  const selected = catalog?.assets.find((asset) => asset.asset_id === selectedId) ?? null;
  const filteredAssets = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return (catalog?.assets ?? []).filter((asset) => (
      (gender === "all" || asset.gender === gender)
      && (age === "all" || asset.age_range === age)
      && (!normalized || `${asset.display_name} ${asset.voice_prompt} ${asset.timbre_tags.join(" ")}`.toLowerCase().includes(normalized))
    ));
  }, [age, catalog?.assets, gender, search]);

  useEffect(() => {
    if (!selected) return;
    setReferenceText(selected.reference_text);
    setPrompt(selected.voice_prompt);
    setPresetId(selected.generation_preset_id);
  }, [selected?.asset_id, selected?.reference_text, selected?.voice_prompt, selected?.generation_preset_id]);

  const pending = catalog?.assets.some((asset) => asset.versions.some((version) => version.status === "queued" || version.status === "running")) ?? false;
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => { void loadCatalog(); }, 1500);
    return () => window.clearInterval(timer);
  }, [pending]);

  async function runAction(action: () => Promise<VoiceCatalogAsset>, success: string): Promise<void> {
    if (!selected) return;
    setBusy(selected.asset_id);
    setMessage("");
    try {
      const next = await action();
      setCatalog((current) => current ? { ...current, assets: current.assets.map((asset) => asset.asset_id === next.asset_id ? next : asset) } : current);
      setMessage(success);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(null);
    }
  }

  async function saveAsset(): Promise<void> {
    if (!selected) return;
    await runAction(
      () => updateVoiceCatalogAsset(selected.asset_id, { voice_prompt: prompt.trim(), reference_text: referenceText.trim(), generation_preset_id: presetId }),
      "声线描述和参考文本已保存",
    );
  }

  async function generateCurrent(): Promise<void> {
    if (!selected) return;
    await runAction(
      () => generateVoiceCatalogAsset(selected.asset_id, { voice_prompt: prompt.trim(), reference_text: referenceText.trim(), preset_id: presetId }),
      "已加入质量优先生成队列，可在版本历史中查看进度",
    );
  }

  async function generateMissing(): Promise<void> {
    const missing = (catalog?.assets ?? []).filter((asset) => !asset.versions.some((version) => version.status === "complete" && version.audio_url));
    if (!missing.length) {
      setMessage("当前筛选范围内都已有完成的参考音频");
      return;
    }
    setBusy("batch");
    setMessage(`正在将 ${missing.length} 个音色加入质量优先队列…`);
    try {
      for (const asset of missing) {
        const next = await generateVoiceCatalogAsset(asset.asset_id, { preset_id: asset.generation_preset_id, reference_text: asset.reference_text, voice_prompt: asset.voice_prompt });
        setCatalog((current) => current ? { ...current, assets: current.assets.map((item) => item.asset_id === next.asset_id ? next : item) } : current);
      }
      setMessage(`已加入 ${missing.length} 个音色，GPU 将按队列依次生成`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "批量生成失败");
    } finally {
      setBusy(null);
    }
  }

  async function derive(): Promise<void> {
    if (!selected?.active_version_id) {
      setMessage("请先生成并完成一个基础参考版本");
      return;
    }
    await runAction(
      () => deriveVoiceCatalogAsset(selected.asset_id, { parent_version_id: selected.active_version_id!, variant_name: variantName.trim(), description: variantDescription.trim(), emotion: variantEmotion.trim(), intensity, reference_text: referenceText.trim(), preset_id: presetId }),
      "已加入派生音色队列",
    );
  }

  async function activate(version: VoiceCatalogVersion): Promise<void> {
    if (!selected) return;
    await runAction(() => activateVoiceCatalogVersion(selected.asset_id, version.version_id), "已切换当前试听版本");
  }

  async function remove(version: VoiceCatalogVersion): Promise<void> {
    if (!selected || !window.confirm(`删除“${version.variant_name}”版本及其音频缓存？`)) return;
    await runAction(() => deleteVoiceCatalogVersion(selected.asset_id, version.version_id), "版本和音频缓存已删除");
  }

  const selectedPreset = catalog?.presets.find((preset) => preset.preset_id === presetId);
  const activeVersion = selected?.versions.find((version) => version.version_id === selected.active_version_id) ?? null;

  return (
    <section className="voice-catalog-workbench">
      <header className="voice-catalog-header">
        <div className="voice-catalog-title"><button className="icon-button" title="返回平台首页" onClick={onBack}><ChevronLeft size={17} /></button><div><span className="eyebrow">VOICE CATALOG · QUALITY FIRST</span><h1>基础音色库</h1><p>用统一约 10 秒参考文本横向比较音色，再把满意的版本复用到角色审核和质量路线。</p></div></div>
        <div className="voice-catalog-actions"><button className="secondary-button" onClick={() => void loadCatalog()} disabled={busy !== null}><RefreshCw size={14} />刷新</button><button className="primary-button" onClick={() => void generateMissing()} disabled={busy !== null || !filteredAssets.length}><Sparkles size={14} />生成当前筛选的缺失音色</button></div>
      </header>
      <div className="voice-catalog-layout">
        <aside className="voice-catalog-list-pane">
          <div className="voice-catalog-filter-head"><div><span className="eyebrow">ARCHETYPE LIBRARY</span><strong>{filteredAssets.length} / {catalog?.assets.length ?? 0} 个基础音色</strong></div><Filter size={15} /></div>
          <div className="voice-catalog-filters"><input value={search} placeholder="搜索名称、标签或描述" onChange={(event) => setSearch(event.target.value)} /><div><select value={gender} onChange={(event) => setGender(event.target.value as typeof gender)}><option value="all">全部性别</option><option value="female">女声</option><option value="male">男声</option></select><select value={age} onChange={(event) => setAge(event.target.value as typeof age)}><option value="all">全部年龄感</option><option value="young_adult">青年</option><option value="adult">成年</option><option value="middle_aged">中老年</option></select></div></div>
          <div className="voice-catalog-list">{filteredAssets.map((asset) => {
            const complete = asset.versions.filter((version) => version.status === "complete" && version.audio_url).length;
            const latest = asset.versions[asset.versions.length - 1];
            return <button key={asset.asset_id} className={`voice-catalog-item ${selectedId === asset.asset_id ? "selected" : ""}`} onClick={() => setSelectedId(asset.asset_id)}><span className={`voice-catalog-avatar voice-catalog-avatar--${asset.gender}`}><Volume2 size={15} /></span><span><strong>{asset.display_name}</strong><small>{genderLabels[asset.gender]} · {ageLabels[asset.age_range]} · {complete ? `${complete} 个版本` : latest ? versionState(latest) : "未生成"}</small></span>{complete ? <Check size={14} /> : latest?.status === "running" || latest?.status === "queued" ? <LoaderCircle size={14} className="spin" /> : <span className="voice-catalog-dot" />}</button>;
          })}</div>
        </aside>

        {selected ? <main className="voice-catalog-detail">
          <div className="voice-catalog-detail-head"><div><span className="eyebrow">{selected.category.replace("_", " / ").toUpperCase()}</span><h2>{selected.display_name}</h2><div className="tag-row">{selected.timbre_tags.map((tag) => <span key={tag}>{tag}</span>)}{selected.delivery_tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div><span className="catalog-confidence">画像置信度 {Math.round(selected.confidence * 100)}%</span></div>
          <div className="voice-catalog-content-grid">
            <section className="voice-catalog-profile-card"><header><div><span className="eyebrow">VOICE PROFILE</span><h3>角色审核式声线描述</h3></div><FileAudio size={18} /></header><label><span>结构化生成提示词</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label><div className="catalog-tag-groups"><div><small>声学属性</small><div className="tag-row">{selected.timbre_tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div><div><small>表达方式</small><div className="tag-row">{selected.delivery_tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div><div><small>约束条件</small><div className="tag-row tag-row--muted">{selected.voice_constraints.map((tag) => <span key={tag}>{tag}</span>)}</div></div><div><small>建议场景</small><div className="tag-row tag-row--muted">{selected.use_cases.map((tag) => <span key={tag}>{tag}</span>)}</div></div></div><p className="catalog-rationale">{selected.rationale}</p></section>
            <section className="voice-catalog-generation-card"><header><div><span className="eyebrow">REFERENCE AUDIO</span><h3>约 10 秒质量参考</h3></div><WandSparkles size={18} /></header><label><span>朗读文本 <small>{referenceText.length} / 220 字</small></span><textarea value={referenceText} onChange={(event) => setReferenceText(event.target.value)} /></label><div className="catalog-reference-presets">{catalog?.reference_texts.map((item) => <button key={item.text_id} className="text-button" onClick={() => setReferenceText(item.text)}>{item.display_name}</button>)}</div><label><span>生成方案</span><select value={presetId} onChange={(event) => setPresetId(event.target.value)}>{catalog?.presets.map((preset: VoiceCatalogPreset) => <option key={preset.preset_id} value={preset.preset_id}>{preset.display_name}</option>)}</select></label>{selectedPreset && <p className="catalog-preset-help">{selectedPreset.description} · CFG {selectedPreset.cfg_value} · 推理步数 {selectedPreset.inference_timesteps} · 目标 {selectedPreset.target_duration_seconds} 秒</p>}<div className="catalog-action-row"><button className="secondary-button" onClick={() => void saveAsset()} disabled={busy !== null}><Save size={14} />保存描述</button><button className="primary-button" onClick={() => void generateCurrent()} disabled={busy !== null || !referenceText.trim() || !prompt.trim()}><Sparkles size={14} />生成高质量参考</button></div></section>
          </div>

          <section className="voice-catalog-preview-card"><header><div><span className="eyebrow">LISTEN & COMPARE</span><h3>当前试听</h3></div>{activeVersion && <span>{activeVersion.variant_name} · {versionState(activeVersion)}</span>}</header>{activeVersion?.audio_url ? <><Waveform src={activeVersion.audio_url} color={colorByGender[selected.gender]} barCount={100} /><AudioPlayer src={activeVersion.audio_url} label={`${selected.display_name}当前版本`} /></> : <div className="voice-catalog-empty"><Play size={18} /><span>{activeVersion ? `当前版本${versionState(activeVersion)}，生成完成后可试听` : "还没有参考音频，先生成一个质量优先版本"}</span></div>}</section>

          <div className="voice-catalog-lower-grid"><section className="voice-catalog-history-card"><header><div><span className="eyebrow">VERSION HISTORY</span><h3>历史版本与缓存</h3></div><Clock3 size={17} /></header>{selected.versions.length ? <div className="voice-catalog-version-list">{[...selected.versions].reverse().map((version) => <article key={version.version_id} className={version.version_id === selected.active_version_id ? "active" : ""}><div className="voice-catalog-version-copy"><strong>{version.variant_name}</strong><small>{version.source === "derived" ? `派生 · ${version.variant_description}` : "基础生成"} · {formatDate(version.created_at)}</small>{version.error && <em><CircleAlert size={12} />{version.error}</em>}</div><span className={`catalog-version-status catalog-version-status--${version.status}`}>{versionState(version)}</span><div className="voice-catalog-version-actions">{version.audio_url && <button className="text-button" onClick={() => void activate(version)} disabled={version.version_id === selected.active_version_id}><Check size={13} />{version.version_id === selected.active_version_id ? "当前" : "试听此版"}</button>}<button className="icon-button" title="删除版本和缓存" onClick={() => void remove(version)}><Trash2 size={13} /></button></div></article>)}</div> : <div className="voice-catalog-empty">生成后这里会保留可回看的历史版本。</div>}</section>
            <section className="voice-catalog-derive-card"><header><div><span className="eyebrow">DERIVATIVE VOICE</span><h3>从当前音色派生</h3></div><CopyPlus size={17} /></header><p>保持角色核心身份不变，只改变情绪和表达方式。派生版本会单独保留，方便横向试听。</p><label><span>版本名称</span><input value={variantName} onChange={(event) => setVariantName(event.target.value)} /></label><label><span>表达描述</span><textarea value={variantDescription} onChange={(event) => setVariantDescription(event.target.value)} /></label><label><span>情绪标签 <small>强度 {Math.round(intensity * 100)}%</small></span><input value={variantEmotion} onChange={(event) => setVariantEmotion(event.target.value)} /></label><input className="catalog-intensity" type="range" min="0" max="1" step="0.05" value={intensity} onChange={(event) => setIntensity(Number(event.target.value))} /><button className="primary-button" onClick={() => void derive()} disabled={busy !== null || !selected.active_version_id}><CopyPlus size={14} />生成派生参考</button></section>
          </div>
          {message && <p className="voice-catalog-message">{message}</p>}
        </main> : <div className="voice-catalog-empty voice-catalog-empty--page"><Volume2 size={26} /><span>正在读取音色库…</span></div>}
      </div>
    </section>
  );
}
