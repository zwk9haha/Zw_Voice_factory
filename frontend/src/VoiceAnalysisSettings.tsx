import { ArrowDown, ArrowUp, Cloud, KeyRound, Plus, PlugZap, RefreshCw, Save, Server, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchVoiceAnalysisConfig, fetchVoiceAnalysisModels, testVoiceAnalysisProfile, updateVoiceAnalysisConfig } from "./api";
import type { VoiceAnalysisApiProtocol, VoiceAnalysisCloudProfile, VoiceAnalysisConfiguration, VoiceAnalysisProvider, VoiceInferenceMode } from "./types";

interface VoiceAnalysisSettingsProps {
  inferenceMode: VoiceInferenceMode;
  applyRequest?: number;
  onApplied?: (configuration: VoiceAnalysisConfiguration) => void;
  onError?: (message: string) => void;
}

interface DraftProfile extends VoiceAnalysisCloudProfile {
  api_key: string;
  clear_api_key: boolean;
}

const providerLabels: Record<VoiceAnalysisProvider, string> = {
  custom: "自定义服务",
  qwen: "通义千问",
  kimi: "Kimi",
  doubao: "豆包",
  gemini: "Gemini",
};

const providerUrls: Record<VoiceAnalysisProvider, string> = {
  custom: "",
  qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  kimi: "https://api.moonshot.cn/v1",
  doubao: "https://ark.cn-beijing.volces.com/api/v3",
  gemini: "https://generativelanguage.googleapis.com/v1beta/openai",
};

const providerProtocols: Record<VoiceAnalysisProvider, VoiceAnalysisApiProtocol> = {
  custom: "responses",
  qwen: "chat_completions",
  kimi: "chat_completions",
  doubao: "chat_completions",
  gemini: "chat_completions",
};

const healthLabels: Record<VoiceAnalysisCloudProfile["health"], string> = {
  unknown: "未测试",
  healthy: "正常",
  failed: "失败",
  cooldown: "冷却中",
};

function profileId(): string {
  return `cloud-${typeof crypto.randomUUID === "function" ? crypto.randomUUID() : Date.now().toString(36)}`;
}

function toDrafts(configuration: VoiceAnalysisConfiguration): DraftProfile[] {
  return configuration.profiles.map((profile) => ({
    ...profile,
    api_key: "",
    clear_api_key: false,
  }));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败";
}

export function VoiceAnalysisSettings({ inferenceMode, applyRequest = 0, onApplied, onError }: VoiceAnalysisSettingsProps) {
  const [failoverEnabled, setFailoverEnabled] = useState(true);
  const [cloudParallelism, setCloudParallelism] = useState(4);
  const [cloudDirectorBatchSize, setCloudDirectorBatchSize] = useState(48);
  const [profiles, setProfiles] = useState<DraftProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>("load");
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  const handledApplyRequestRef = useRef(0);

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.profile_id === selectedProfileId) ?? profiles[0] ?? null,
    [profiles, selectedProfileId],
  );

  useEffect(() => {
    let active = true;
    fetchVoiceAnalysisConfig().then((configuration) => {
      if (!active) return;
      const nextProfiles = toDrafts(configuration);
      setFailoverEnabled(configuration.failover_enabled);
      setCloudParallelism(configuration.cloud_parallelism);
      setCloudDirectorBatchSize(configuration.cloud_director_batch_size);
      setProfiles(nextProfiles);
      setSelectedProfileId(nextProfiles[0]?.profile_id ?? null);
      setBusy(null);
      setLoaded(true);
    }).catch((error: unknown) => {
      if (!active) return;
      setMessage(errorMessage(error));
      setBusy(null);
      setLoaded(true);
    });
    return () => { active = false; };
  }, []);

  function applyConfiguration(configuration: VoiceAnalysisConfiguration): void {
    setFailoverEnabled(configuration.failover_enabled);
    setCloudParallelism(configuration.cloud_parallelism);
    setCloudDirectorBatchSize(configuration.cloud_director_batch_size);
    setProfiles(() => {
      const next = toDrafts(configuration);
      setSelectedProfileId((selected) => next.some((profile) => profile.profile_id === selected) ? selected : next[0]?.profile_id ?? null);
      return next;
    });
  }

  function updateProfile(profileIdValue: string, update: Partial<DraftProfile>): void {
    setProfiles((current) => current.map((profile) => profile.profile_id === profileIdValue ? { ...profile, ...update } : profile));
  }

  function addProfile(): void {
    const id = profileId();
    setProfiles((current) => [
      ...current,
      {
        profile_id: id,
        name: `云端 API ${current.length + 1}`,
        provider: "custom",
        base_url: "",
        model: "",
        api_protocol: "responses",
        api_key_configured: false,
        api_key: "",
        clear_api_key: false,
        enabled: true,
        priority: current.length + 1,
        health: "unknown",
        last_error: null,
      },
    ]);
    setSelectedProfileId(id);
    setModels([]);
  }

  function removeProfile(profileIdValue: string): void {
    setProfiles((current) => current.filter((profile) => profile.profile_id !== profileIdValue));
    setSelectedProfileId((selected) => selected === profileIdValue ? null : selected);
    setModels([]);
  }

  function moveProfile(profileIdValue: string, direction: -1 | 1): void {
    setProfiles((current) => {
      const index = current.findIndex((profile) => profile.profile_id === profileIdValue);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next.map((profile, priority) => ({ ...profile, priority: priority + 1 }));
    });
  }

  async function saveConfiguration(successMessage = "文本分析配置已保存"): Promise<VoiceAnalysisConfiguration> {
    const primary = profiles[0];
    const configuration = await updateVoiceAnalysisConfig({
      backend: inferenceMode,
      provider: primary?.provider ?? "custom",
      base_url: primary?.base_url ?? "",
      model: primary?.model ?? "",
      api_protocol: primary?.api_protocol ?? "chat_completions",
      api_key: primary?.api_key.trim() || null,
      failover_enabled: failoverEnabled,
      cloud_parallelism: cloudParallelism,
      cloud_director_batch_size: cloudDirectorBatchSize,
      profiles: profiles.map((profile) => ({
        profile_id: profile.profile_id,
        name: profile.name.trim(),
        provider: profile.provider,
        base_url: profile.base_url.trim(),
        model: profile.model.trim(),
        api_protocol: profile.api_protocol,
        api_key: profile.api_key.trim() || null,
        clear_api_key: profile.clear_api_key,
        enabled: profile.enabled,
      })),
    });
    applyConfiguration(configuration);
    setMessage(successMessage);
    return configuration;
  }

  async function save(): Promise<void> {
    setBusy("save");
    setMessage("");
    try {
      await saveConfiguration();
    } catch (error: unknown) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (!applyRequest || !loaded || busy !== null || handledApplyRequestRef.current === applyRequest) return;
    handledApplyRequestRef.current = applyRequest;
    setBusy("apply");
    setMessage("正在应用推理模板");
    saveConfiguration("推理模板已应用，后续分析将按此模式执行")
      .then((configuration) => {
        if (handledApplyRequestRef.current === applyRequest) onApplied?.(configuration);
      })
      .catch((error: unknown) => {
        const detail = errorMessage(error);
        setMessage(detail);
        onError?.(detail);
      })
      .finally(() => {
        if (handledApplyRequestRef.current === applyRequest) setBusy(null);
      });
  }, [applyRequest, loaded, busy]);

  async function testSelectedProfile(): Promise<void> {
    if (!selectedProfile) return;
    setBusy(`test:${selectedProfile.profile_id}`);
    setMessage(`正在用短文本探针测试 ${selectedProfile.name}`);
    try {
      await saveConfiguration("配置已保存，正在发送短文本探针");
      applyConfiguration(await testVoiceAnalysisProfile(selectedProfile.profile_id));
      setMessage(`${selectedProfile.name} 连接正常，未提交项目文本`);
    } catch (error: unknown) {
      setMessage(errorMessage(error));
      const latest = await fetchVoiceAnalysisConfig().catch(() => null);
      if (latest) applyConfiguration(latest);
    } finally {
      setBusy(null);
    }
  }

  async function loadModels(): Promise<void> {
    if (!selectedProfile) return;
    setBusy(`models:${selectedProfile.profile_id}`);
    setMessage("");
    try {
      const catalog = await fetchVoiceAnalysisModels({
        profile_id: selectedProfile.profile_id,
        provider: selectedProfile.provider,
        base_url: selectedProfile.base_url.trim(),
        api_key: selectedProfile.api_key.trim() || null,
      });
      const availableModels = catalog.models.map((model) => model.id);
      setModels(availableModels);
      if (!selectedProfile.model.trim() && availableModels.length) {
        updateProfile(selectedProfile.profile_id, { model: availableModels[0] });
      }
      setMessage(`已读取 ${availableModels.length} 个可用模型`);
    } catch (error: unknown) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  function changeProvider(provider: VoiceAnalysisProvider): void {
    if (!selectedProfile) return;
    updateProfile(selectedProfile.profile_id, {
      provider,
      base_url: providerUrls[provider],
      api_protocol: providerProtocols[provider],
      health: "unknown",
      last_error: null,
    });
    setModels([]);
  }

  function clearSelectedApiKey(): void {
    if (!selectedProfile) return;
    updateProfile(selectedProfile.profile_id, {
      api_key: "",
      api_key_configured: false,
      clear_api_key: true,
      health: "unknown",
      last_error: null,
    });
    setMessage(`${selectedProfile.name} 的 Key 将在保存后清除`);
  }

  return (
    <section className="voice-analysis-settings" aria-label="文本分析后端设置">
      <div className="voice-analysis-settings__heading">
        <div><span className="eyebrow">TEXT ANALYSIS ROUTING</span><h3>文本分析路由</h3></div>
        <span className="voice-analysis-settings__state">{busy === "load" ? "读取中" : inferenceMode === "local" ? "本地模型" : `${profiles.filter((profile) => profile.enabled).length} 个云端端点`}</span>
      </div>
      <div className="voice-analysis-mode voice-analysis-mode--read-only" aria-label="当前文本分析模式">
        <span className={inferenceMode === "cloud" ? "active" : ""}><Cloud size={14} />完全由云端 API 推理</span>
        <span className={inferenceMode === "hybrid" ? "active" : ""}><Sparkles size={14} />本地初筛后云端推理</span>
        <span className={inferenceMode === "local" ? "active" : ""}><Server size={14} />本地模型推理</span>
      </div>
      {inferenceMode !== "local" && (
        <>
          <div className="cloud-route-toolbar">
            <div><strong>故障转移队列</strong><small>按 P1 到 P{Math.max(profiles.length, 1)} 顺序调用</small></div>
            <label className="cloud-route-toggle"><input type="checkbox" checked={failoverEnabled} onChange={(event) => setFailoverEnabled(event.target.checked)} /><span>自动转移</span></label>
            <button type="button" className="icon-button" title="添加云端 API" disabled={busy !== null} onClick={addProfile}><Plus size={16} /></button>
          </div>
          <div className="cloud-performance-controls">
            <label><span>并发请求</span><input type="number" min="1" max="8" value={cloudParallelism} onChange={(event) => setCloudParallelism(Math.max(1, Math.min(8, Number(event.target.value) || 1)))} /></label>
            <label><span>导演批次</span><input type="number" min="8" max="96" step="8" value={cloudDirectorBatchSize} onChange={(event) => setCloudDirectorBatchSize(Math.max(8, Math.min(96, Number(event.target.value) || 8)))} /></label>
          </div>
          <div className="cloud-route-list">
            {profiles.map((profile, index) => (
              <div className={`cloud-route-row ${profile.profile_id === selectedProfile?.profile_id ? "selected" : ""} ${profile.enabled ? "" : "disabled"}`} key={profile.profile_id}>
                <button type="button" className="cloud-route-select" onClick={() => { setSelectedProfileId(profile.profile_id); setModels([]); }}>
                  <b>P{index + 1}</b>
                  <span><strong>{profile.name}</strong><small>{profile.model || "未选择模型"} · {profile.base_url || "未填写 Base URL"}</small></span>
                  <em className={`health-${profile.health}`} title={profile.last_error ?? healthLabels[profile.health]}>{healthLabels[profile.health]}</em>
                </button>
                <label className="cloud-route-enable" title={profile.enabled ? "禁用端点" : "启用端点"}><input type="checkbox" checked={profile.enabled} onChange={(event) => updateProfile(profile.profile_id, { enabled: event.target.checked })} /></label>
                <button type="button" className="icon-button" title="上移" disabled={busy !== null || index === 0} onClick={() => moveProfile(profile.profile_id, -1)}><ArrowUp size={14} /></button>
                <button type="button" className="icon-button" title="下移" disabled={busy !== null || index === profiles.length - 1} onClick={() => moveProfile(profile.profile_id, 1)}><ArrowDown size={14} /></button>
                <button type="button" className="icon-button danger-text" title="删除端点" disabled={busy !== null} onClick={() => removeProfile(profile.profile_id)}><Trash2 size={14} /></button>
              </div>
            ))}
            {!profiles.length && <p className="cloud-route-empty">还没有云端 API，请点击加号添加 P1。</p>}
          </div>
          {selectedProfile && (
            <div className="cloud-profile-editor">
              <div className="cloud-profile-editor__heading"><strong>P{profiles.findIndex((profile) => profile.profile_id === selectedProfile.profile_id) + 1} 端点配置</strong><span>{selectedProfile.api_key_configured ? "Key 已保存" : "Key 未配置"}</span></div>
              <div className="voice-analysis-cloud-fields">
                <label><span>名称</span><input value={selectedProfile.name} onChange={(event) => updateProfile(selectedProfile.profile_id, { name: event.target.value })} placeholder="例如：主线 Gemini" /></label>
                <label><span>服务商</span><select value={selectedProfile.provider} onChange={(event) => changeProvider(event.target.value as VoiceAnalysisProvider)}>{Object.entries(providerLabels).map(([provider, label]) => <option key={provider} value={provider}>{label}</option>)}</select></label>
                <label className="cloud-profile-wide"><span>Base URL</span><input value={selectedProfile.base_url} onChange={(event) => { updateProfile(selectedProfile.profile_id, { base_url: event.target.value, health: "unknown" }); setModels([]); }} placeholder="https://.../v1" /></label>
                <div className="voice-analysis-protocol-field"><span>API 协议</span><div className="voice-analysis-protocol" role="group" aria-label="云端 API 协议"><button type="button" className={selectedProfile.api_protocol === "responses" ? "active" : ""} onClick={() => updateProfile(selectedProfile.profile_id, { api_protocol: "responses" })}>Responses</button><button type="button" className={selectedProfile.api_protocol === "chat_completions" ? "active" : ""} onClick={() => updateProfile(selectedProfile.profile_id, { api_protocol: "chat_completions" })}>Chat Completions</button></div></div>
                <label><span>模型名称 {models.length > 0 && <em>{models.length} 个可用</em>}</span><div className="voice-analysis-model-picker"><input list={`voice-analysis-models-${selectedProfile.profile_id}`} value={selectedProfile.model} onChange={(event) => updateProfile(selectedProfile.profile_id, { model: event.target.value })} placeholder="读取列表或手动输入" /><button type="button" title="读取可用模型" disabled={busy !== null || !selectedProfile.base_url.trim() || (!selectedProfile.api_key.trim() && !selectedProfile.api_key_configured)} onClick={() => void loadModels()}><RefreshCw size={14} className={busy === `models:${selectedProfile.profile_id}` ? "spin" : ""} /></button></div><datalist id={`voice-analysis-models-${selectedProfile.profile_id}`}>{models.map((model) => <option key={model} value={model} />)}</datalist></label>
                <label className="cloud-profile-wide"><span><KeyRound size={12} />API Key {selectedProfile.api_key_configured && <em>留空保持不变</em>}</span><input type="password" value={selectedProfile.api_key} onChange={(event) => updateProfile(selectedProfile.profile_id, { api_key: event.target.value, clear_api_key: false })} placeholder={selectedProfile.api_key_configured ? "••••••••" : "输入 API Key"} autoComplete="off" /></label>
              </div>
              <div className="cloud-profile-actions">
                {selectedProfile.api_key_configured && <button type="button" className="secondary-button danger-text" disabled={busy !== null} onClick={clearSelectedApiKey}><KeyRound size={14} />清除 Key</button>}
                <button type="button" className="secondary-button" title="仅发送固定短文本探针，不提交项目或导演文件内容" disabled={busy !== null || (!selectedProfile.api_key.trim() && !selectedProfile.api_key_configured)} onClick={() => void testSelectedProfile()}><PlugZap size={14} />{busy === `test:${selectedProfile.profile_id}` ? "测试中" : "测试连接"}</button>
              </div>
            </div>
          )}
        </>
      )}
      <div className="voice-analysis-settings__footer">
        <span className="voice-analysis-settings__message" title={message}>{message}</span>
        <button type="button" className="primary-button" disabled={busy !== null} onClick={() => void save()}><Save size={14} />{busy === "save" ? "保存中" : "保存设置"}</button>
      </div>
    </section>
  );
}
