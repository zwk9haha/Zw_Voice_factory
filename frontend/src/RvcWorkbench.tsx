import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AudioLines, CheckCircle2, CircleAlert, Database, FileText, Gauge, GitCompareArrows, Layers3, LoaderCircle, Play, Save, Search, ShieldCheck, Square, Timer, WandSparkles, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { cancelRvcTrainingJob, createRvcBenchmark, createRvcPreview, createRvcTrainingJob, fetchRvcWorkspace, reviewRvcBenchmark, updateRvcInferenceProfile, updateRvcSettings } from "./api";
import { AudioPlayer } from "./AudioPlayer";
import type { RvcInferenceProfile, RvcPreviewResult, RvcPreviewSource, RvcRoute, RvcSettingsUpdate, RvcTrainingOptions, RvcTrainingPurpose, RvcTrainingStage, RvcWorkspace } from "./types";
import { Waveform } from "./Waveform";

interface RvcWorkbenchProps {
  open: boolean;
  origin: { x: number; y: number };
  projectId: string;
  onClose: () => void;
}

const DEFAULT_RVC_PREVIEW_TEXT = "夜色沿着长街慢慢沉下来，我停在路口，确认风声里没有异常，才继续向前。";

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return minutes > 0 ? `${minutes} 分 ${remaining} 秒` : `${remaining} 秒`;
}

function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分 ${remaining} 秒`;
  if (minutes > 0) return `${minutes} 分 ${remaining} 秒`;
  return `${remaining} 秒`;
}

const TRAINING_STAGE_LABELS: Record<RvcTrainingStage, string> = {
  queued: "排队中",
  preparing_material: "准备训练材料",
  preprocessing: "预处理音频",
  extracting_pitch: "提取音高",
  extracting_features: "提取音色特征",
  starting_training: "启动 RVC 训练",
  training: "训练音色模型",
  building_index: "构建检索索引",
  finalizing: "整理模型资产",
  complete: "训练完成",
  failed: "训练失败",
  cancelled: "已取消",
};

function trainingStageLabel(stage: RvcTrainingStage | undefined, status: string | undefined): string {
  if (stage && stage in TRAINING_STAGE_LABELS) return TRAINING_STAGE_LABELS[stage];
  if (status === "queued") return "排队中";
  if (status === "running") return "运行中";
  if (status === "complete") return "训练完成";
  if (status === "failed") return "训练失败";
  if (status === "cancelled") return "已取消";
  return "暂无任务";
}

export function RvcWorkbench({ open, origin, projectId, onClose }: RvcWorkbenchProps) {
  const queryClient = useQueryClient();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [selectedCharacterId, setSelectedCharacterId] = useState("");
  const [options, setOptions] = useState<RvcTrainingOptions | null>(null);
  const [busyAction, setBusyAction] = useState("");
  const [feedback, setFeedback] = useState("");
  const [previewSource, setPreviewSource] = useState<RvcPreviewSource>("voxcpm2");
  const [previewText, setPreviewText] = useState(DEFAULT_RVC_PREVIEW_TEXT);
  const [previewResult, setPreviewResult] = useState<RvcPreviewResult | null>(null);
  const [previewFeedback, setPreviewFeedback] = useState("");
  const [trainingPurpose, setTrainingPurpose] = useState<RvcTrainingPurpose>("quality_stability");
  const [benchmarkRoute, setBenchmarkRoute] = useState<RvcRoute>("quality");
  const [profileDraft, setProfileDraft] = useState<RvcInferenceProfile | null>(null);
  const [preferencePercent, setPreferencePercent] = useState(70);
  const [identityImproved, setIdentityImproved] = useState(false);
  const [intelligibilityPreserved, setIntelligibilityPreserved] = useState(false);
  const [expressionPreserved, setExpressionPreserved] = useState(false);
  const [reviewNotes, setReviewNotes] = useState("");
  const [characterQuery, setCharacterQuery] = useState("");
  const [characterFilter, setCharacterFilter] = useState<"all" | "attention" | "approved">("all");
  const { data: workspace, isFetching } = useQuery({
    queryKey: ["rvc-workspace", projectId],
    queryFn: () => fetchRvcWorkspace(projectId),
    enabled: open,
    retry: false,
    refetchInterval: open ? 2_000 : false,
  });

  useEffect(() => {
    if (!open) return;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  useEffect(() => {
    if (!workspace) return;
    if (!selectedCharacterId || !workspace.characters.some((item) => item.character_id === selectedCharacterId)) {
      setSelectedCharacterId(workspace.characters[0]?.character_id ?? "");
    }
    setOptions((current) => current ?? workspace.settings.training_options);
  }, [selectedCharacterId, workspace]);

  const selectedCharacter = workspace?.characters.find((item) => item.character_id === selectedCharacterId) ?? null;
  const activeJob = useMemo(() => {
    if (!workspace || !selectedCharacter) return null;
    return workspace.jobs.find((job) => job.job_id === selectedCharacter.active_job_id)
      ?? workspace.jobs.find((job) => job.character_id === selectedCharacter.character_id)
      ?? null;
  }, [selectedCharacter, workspace]);
  const selectedModel = workspace?.models.find((model) => model.model_id === selectedCharacter?.selected_model_id) ?? null;
  const activeJobs = workspace?.jobs.filter((job) => job.status === "queued" || job.status === "running") ?? [];
  const awaitingReviews = workspace?.benchmarks.filter((report) => report.status === "complete" && report.decision === "pending") ?? [];
  const approvedModels = workspace?.models.filter((model) => model.status === "approved").length ?? 0;
  const filteredCharacters = (workspace?.characters ?? []).filter((character) => {
    const queryMatches = !characterQuery.trim() || character.display_name.toLocaleLowerCase().includes(characterQuery.trim().toLocaleLowerCase());
    if (!queryMatches) return false;
    if (characterFilter === "approved") return character.quality_approved || character.fast_approved;
    if (characterFilter === "attention") {
      return Boolean(character.active_job_id)
        || !character.training_ready
        || (Boolean(character.selected_model_id) && !character.quality_approved && !character.fast_approved);
    }
    return true;
  });
  const activeBenchmark = useMemo(() => {
    if (!workspace || !selectedCharacter || !selectedModel) return null;
    return workspace.benchmarks.find((report) => (
      report.character_id === selectedCharacter.character_id
      && report.model_id === selectedModel.model_id
      && report.route === benchmarkRoute
    )) ?? null;
  }, [benchmarkRoute, selectedCharacter, selectedModel, workspace]);

  useEffect(() => {
    const profile = selectedModel?.inference_profiles[benchmarkRoute] ?? null;
    setProfileDraft(profile ? { ...profile } : null);
  }, [benchmarkRoute, selectedModel?.model_id, selectedModel?.profile_fingerprints[benchmarkRoute]]);

  useEffect(() => {
    setPreviewResult(null);
    setPreviewFeedback("");
  }, [selectedCharacterId, selectedCharacter?.selected_model_id]);

  useEffect(() => {
    setPreviewResult(null);
    setPreviewFeedback("");
  }, [previewSource]);

  useEffect(() => {
    setPreferencePercent(activeBenchmark?.preference_percent ?? 70);
    setIdentityImproved(activeBenchmark?.identity_improved ?? false);
    setIntelligibilityPreserved(activeBenchmark?.intelligibility_preserved ?? false);
    setExpressionPreserved(activeBenchmark?.expression_preserved ?? false);
    setReviewNotes(activeBenchmark?.reviewer_notes ?? "");
  }, [activeBenchmark?.benchmark_id, activeBenchmark?.decision]);

  async function applySettings(update: RvcSettingsUpdate, action: string) {
    setBusyAction(action);
    setFeedback("");
    try {
      const next = await updateRvcSettings(projectId, update);
      queryClient.setQueryData<RvcWorkspace>(["rvc-workspace", projectId], next);
      if (next.settings.training_options) setOptions(next.settings.training_options);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "RVC 设置更新失败");
    } finally {
      setBusyAction("");
    }
  }

  async function startTraining() {
    if (!selectedCharacter || !options) return;
    setBusyAction("train");
    setFeedback("");
    try {
      await createRvcTrainingJob(projectId, selectedCharacter.character_id, options, trainingPurpose);
      await queryClient.invalidateQueries({ queryKey: ["rvc-workspace", projectId] });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "RVC 训练任务提交失败");
    } finally {
      setBusyAction("");
    }
  }

  async function startBenchmark() {
    if (!selectedCharacter || !selectedModel?.index_path) return;
    setBusyAction("benchmark");
    setFeedback("");
    try {
      await createRvcBenchmark(projectId, selectedCharacter.character_id, selectedModel.model_id, benchmarkRoute);
      await queryClient.invalidateQueries({ queryKey: ["rvc-workspace", projectId] });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "RVC 基准任务提交失败");
    } finally {
      setBusyAction("");
    }
  }

  async function saveInferenceProfile() {
    if (!selectedModel || !profileDraft) return;
    setBusyAction("save-profile");
    setFeedback("");
    try {
      const next = await updateRvcInferenceProfile(
        projectId,
        selectedModel.model_id,
        benchmarkRoute,
        profileDraft,
      );
      queryClient.setQueryData<RvcWorkspace>(["rvc-workspace", projectId], next);
      setFeedback("路线参数已保存；该路线原有批准已失效，请重新运行基准");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "RVC 路线参数保存失败");
    } finally {
      setBusyAction("");
    }
  }

  async function reviewBenchmark(approved: boolean) {
    if (!activeBenchmark) return;
    setBusyAction("benchmark-review");
    setFeedback("");
    try {
      const next = await reviewRvcBenchmark(activeBenchmark.benchmark_id, {
        approved,
        preference_percent: preferencePercent,
        identity_improved: identityImproved,
        intelligibility_preserved: intelligibilityPreserved,
        expression_preserved: expressionPreserved,
        notes: reviewNotes,
      });
      queryClient.setQueryData<RvcWorkspace>(["rvc-workspace", projectId], next);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "RVC 基准审核失败");
    } finally {
      setBusyAction("");
    }
  }

  async function cancelTraining() {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) return;
    setBusyAction("cancel");
    try {
      await cancelRvcTrainingJob(activeJob.job_id);
      await queryClient.invalidateQueries({ queryKey: ["rvc-workspace", projectId] });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "RVC 训练取消失败");
    } finally {
      setBusyAction("");
    }
  }

  async function generatePreview() {
    if (!selectedCharacter || !selectedModel?.index_path || !previewText.trim()) return;
    setBusyAction("preview");
    setPreviewFeedback("");
    try {
      const result = await createRvcPreview(
        projectId,
        selectedCharacter.character_id,
        previewText.trim(),
        previewSource,
      );
      setPreviewResult(result);
    } catch (error) {
      setPreviewFeedback(error instanceof Error ? error.message : "RVC A/B 试听生成失败");
    } finally {
      setBusyAction("");
    }
  }

  const overlayStyle = {
    "--rvc-origin-x": `${origin.x}px`,
    "--rvc-origin-y": `${origin.y}px`,
  } as CSSProperties;
  const optionsReady = options ?? workspace?.settings.training_options;
  const trainingActive = activeJob?.status === "queued" || activeJob?.status === "running";
  const trainingMinimum = trainingPurpose === "quality_stability" ? 180 : 480;

  return (
    <section
      className={`rvc-workbench ${open ? "rvc-workbench--open" : ""}`}
      style={overlayStyle}
      aria-hidden={!open}
      aria-label="RVC 速度路线工作台"
    >
      <header className="rvc-workbench__header">
        <div className="rvc-workbench__brand">
          <span><Gauge size={20} /></span>
          <div><small>速度路线 / 稳定层</small><strong>RVC 工作台</strong></div>
        </div>
        <div className="rvc-runtime-state">
          <Activity size={14} />
          <span>{workspace?.runtime_detail ?? (isFetching ? "正在读取 RVC 运行时" : "RVC 运行时未连接")}</span>
        </div>
        <button ref={closeButtonRef} className="icon-button rvc-close-button" title="关闭 RVC 工作台" onClick={onClose}><X size={18} /></button>
      </header>

      <div className="rvc-overview" aria-label="RVC 生产概览">
        <div><Layers3 size={15} /><span><small>训练对象</small><strong>{workspace?.characters.filter((item) => item.train_enabled).length ?? 0} / {workspace?.characters.length ?? 0}</strong></span></div>
        <div className={activeJobs.length ? "active" : ""}><Activity size={15} /><span><small>训练队列</small><strong>{activeJobs.length ? activeJobs.length + " 个运行中" : "空闲"}</strong></span></div>
        <div className={awaitingReviews.length ? "attention" : ""}><CircleAlert size={15} /><span><small>等待审核</small><strong>{awaitingReviews.length}</strong></span></div>
        <div className={approvedModels ? "ready" : ""}><ShieldCheck size={15} /><span><small>已批准模型</small><strong>{approvedModels}</strong></span></div>
      </div>

      <div className="rvc-workbench__body">
        <aside className="rvc-character-pane">
          <div className="rvc-pane-heading"><div><small>训练对象</small><h2>项目角色</h2></div><b>{workspace?.characters.length ?? 0}</b></div>
          <div className="rvc-character-tools">
            <label><Search size={13} /><input value={characterQuery} onChange={(event) => setCharacterQuery(event.target.value)} placeholder="搜索角色" /></label>
            <div role="group" aria-label="角色状态筛选">
              <button className={characterFilter === "all" ? "active" : ""} onClick={() => setCharacterFilter("all")}>全部</button>
              <button className={characterFilter === "attention" ? "active" : ""} onClick={() => setCharacterFilter("attention")}>待处理</button>
              <button className={characterFilter === "approved" ? "active" : ""} onClick={() => setCharacterFilter("approved")}>已批准</button>
            </div>
          </div>
          <div className="rvc-character-list">
            {filteredCharacters.map((character) => (
              <div className={`rvc-character-row ${character.character_id === selectedCharacterId ? "selected" : ""}`} key={character.character_id}>
                <button className="rvc-character-select" onClick={() => setSelectedCharacterId(character.character_id)}>
                  <span className={`rvc-readiness-dot ${character.training_ready ? "ready" : ""}`} />
                  <span><strong>{character.display_name}</strong><small>{formatDuration(character.material_duration_seconds)} / {character.material_count} 段</small></span>
                  {character.selected_model_id && <CheckCircle2 size={14} />}
                </button>
                <label className="rvc-inline-toggle" title="将该角色纳入 RVC 训练计划">
                  <input
                    type="checkbox"
                    checked={character.train_enabled}
                    disabled={busyAction === `train-enabled-${character.character_id}`}
                    onChange={(event) => { void applySettings({ character_id: character.character_id, train_enabled: event.target.checked }, `train-enabled-${character.character_id}`); }}
                  />
                  <span className="toggle-track"><span /></span>
                  <span>训练</span>
                </label>
              </div>
            ))}
            {!filteredCharacters.length && <p className="rvc-empty">{workspace?.characters.length ? "当前筛选没有匹配角色" : "参考计划中暂无可训练角色"}</p>}
          </div>
          <div className="rvc-route-summary">
            <Layers3 size={16} />
            <div><strong>双路线角色资产</strong><small>极速 {workspace?.settings.fast_route_enabled ? "已接入" : "未接入"} · 质量 {workspace?.settings.quality_stability_enabled ? "已接入" : "未接入"}</small></div>
          </div>
        </aside>

        <main className="rvc-training-pane">
          <div className="rvc-pane-heading rvc-training-heading">
            <div><small>TRAINING SESSION</small><h2>{selectedCharacter?.display_name ?? "未选择角色"}</h2></div>
            <span className={`rvc-status-pill ${(selectedCharacter?.material_duration_seconds ?? 0) >= trainingMinimum ? "ready" : ""}`}>{(selectedCharacter?.material_duration_seconds ?? 0) >= trainingMinimum ? "材料就绪" : `缺少 ${Math.max(0, Math.ceil(trainingMinimum - (selectedCharacter?.material_duration_seconds ?? 0)))} 秒 · 锚定补齐`}</span>
          </div>

          <section className="rvc-material-section">
            <header><div><AudioLines size={16} /><strong>训练材料</strong></div><span>{selectedCharacter ? formatDuration(selectedCharacter.material_duration_seconds) : "0 秒"}</span></header>
            <div className="rvc-preview-source" aria-label="RVC 训练用途">
              <button type="button" className={trainingPurpose === "quality_stability" ? "active" : ""} onClick={() => setTrainingPurpose("quality_stability")}>质量稳定 · 3 分钟</button>
              <button type="button" className={trainingPurpose === "fast_identity" ? "active" : ""} onClick={() => setTrainingPurpose("fast_identity")}>极速身份 · 8 分钟</button>
              <button type="button" className={trainingPurpose === "both" ? "active" : ""} onClick={() => setTrainingPurpose("both")}>双路线 · 8 分钟</button>
            </div>
            <div className="rvc-material-meter"><span style={{ width: `${Math.min(100, ((selectedCharacter?.material_duration_seconds ?? 0) / trainingMinimum) * 100)}%` }} /></div>
            {selectedCharacter?.sample_audio_url ? (
              <div className="rvc-material-preview"><Waveform src={selectedCharacter.sample_audio_url} color="violet" /><AudioPlayer src={selectedCharacter.sample_audio_url} label={`${selectedCharacter.display_name} 训练样本`} /></div>
            ) : <p className="rvc-empty">尚无可读取的本地 WAV 训练材料</p>}
          </section>

          {optionsReady && (
            <section className="rvc-parameter-section">
              <header><div><Timer size={16} /><strong>训练参数</strong></div><button className="text-button" disabled={Boolean(busyAction)} onClick={() => { void applySettings({ training_options: optionsReady }, "save-options"); }}><Save size={13} />保存预设</button></header>
              <div className="rvc-parameter-grid">
                <label><span>模型版本</span><select value={optionsReady.version} disabled><option value="v2">RVC V2</option></select></label>
                <label><span>采样率</span><select value={optionsReady.sample_rate} disabled><option value="40k">40 kHz</option></select></label>
                <label><span>音高提取</span><select value={optionsReady.pitch_method} onChange={(event) => setOptions({ ...optionsReady, pitch_method: event.target.value as RvcTrainingOptions["pitch_method"] })}><option value="rmvpe_gpu">RMVPE GPU</option><option value="rmvpe">RMVPE</option></select></label>
                <label><span>训练轮数</span><input type="number" min={20} max={2000} value={optionsReady.epochs} onChange={(event) => setOptions({ ...optionsReady, epochs: Number(event.target.value) })} /></label>
                <label><span>保存间隔</span><input type="number" min={5} max={500} value={optionsReady.save_every_epochs} onChange={(event) => setOptions({ ...optionsReady, save_every_epochs: Number(event.target.value) })} /></label>
                <label><span>批量大小</span><input type="number" min={1} max={32} value={optionsReady.batch_size} onChange={(event) => setOptions({ ...optionsReady, batch_size: Number(event.target.value) })} /></label>
                <label><span>预处理进程</span><input type="number" min={1} max={32} value={optionsReady.process_count} onChange={(event) => setOptions({ ...optionsReady, process_count: Number(event.target.value) })} /></label>
                <label><span>GPU</span><input value={optionsReady.gpu_ids} onChange={(event) => setOptions({ ...optionsReady, gpu_ids: event.target.value })} /></label>
              </div>
              <div className="rvc-binary-options">
                <label title="当前 RVC V2 预训练资产要求启用音高指导"><input type="checkbox" checked={optionsReady.pitch_guidance} disabled />音高指导</label>
                <label><input type="checkbox" checked={optionsReady.cache_gpu} onChange={(event) => setOptions({ ...optionsReady, cache_gpu: event.target.checked })} />特征缓存到显存</label>
              </div>
            </section>
          )}

          <section className="rvc-job-section">
            <header><div><Activity size={16} /><strong>训练监视器</strong></div><span>{activeJob ? trainingStageLabel(activeJob.stage, activeJob.status) : "当前无任务"}</span></header>
            <div className="rvc-job-progress"><span style={{ width: `${activeJob?.progress ?? 0}%` }} /></div>
            <div className="rvc-job-status-line">
              <strong>{activeJob ? trainingStageLabel(activeJob.stage, activeJob.status) : "尚未开始训练"}</strong>
              <span>{activeJob ? `${activeJob.progress}%` : "0%"}</span>
            </div>
            {activeJob && (
              <div className="rvc-job-details">
                <span>{activeJob.current_epoch !== null && activeJob.total_epochs !== null ? `第 ${activeJob.current_epoch} / ${activeJob.total_epochs} 轮` : "等待训练轮次"}</span>
                <span>已运行 {formatElapsed(activeJob.elapsed_seconds)}</span>
              </div>
            )}
            <p className="rvc-job-message">{activeJob?.message ?? "提交训练后，这里会显示真实的处理阶段和训练轮次。"}</p>
            {activeJob?.last_log && <p className="rvc-job-log" title={activeJob.last_log}>最近日志：{activeJob.last_log}</p>}
            <div className="rvc-job-actions">
              <span>{activeJob ? `${activeJob.status} · ${formatElapsed(activeJob.elapsed_seconds)}` : "idle"}</span>
              {trainingActive ? (
                <button className="stop-button" disabled={busyAction === "cancel"} onClick={() => { void cancelTraining(); }}><Square size={13} />取消训练</button>
              ) : (
                <button
                  className="primary-button"
                  disabled={!selectedCharacter || !workspace?.training_runtime_available || Boolean(busyAction)}
                  onClick={() => { void startTraining(); }}
                ><Play size={14} />{(selectedCharacter?.material_duration_seconds ?? 0) >= trainingMinimum ? "训练候选模型" : "构建锚定训练集并训练"}</button>
              )}
            </div>
            {(feedback || activeJob?.error || activeJob?.log_id) && <div className="rvc-feedback">{(feedback || activeJob?.error) && <p>{feedback || activeJob?.error}</p>}{activeJob?.log_id && <a href={`/api/logs/${activeJob.log_id.split("/").map(encodeURIComponent).join("/")}`} target="_blank" rel="noreferrer"><FileText size={13} />查看本次训练日志</a>}</div>}
          </section>
        </main>

        <aside className="rvc-model-pane">
          <div className="rvc-pane-heading"><div><small>STABILITY LAYER</small><h2>模型与绑定</h2></div><Database size={17} /></div>
          <section className="rvc-binding-section">
            <label><span>角色模型</span><select
              value={selectedCharacter?.selected_model_id ?? ""}
              disabled={!selectedCharacter || Boolean(busyAction)}
              onChange={(event) => {
                const modelId = event.target.value || null;
                void applySettings({ character_id: selectedCharacter!.character_id, selected_model_id: modelId, ...(modelId ? {} : { stability_enabled: false, fast_character_enabled: false }) }, "bind-model");
              }}
            ><option value="">未绑定</option>{workspace?.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.label} · {model.status}</option>)}</select></label>
            <div className={`rvc-status-pill ${selectedModel?.status === "approved" ? "ready" : ""}`}>{selectedModel ? `${selectedModel.status} · ${selectedModel.approved_routes.length ? selectedModel.approved_routes.join(" / ") : "尚未批准路线"}` : "未绑定模型"}</div>
            <label className="rvc-setting-row"><span><strong>极速路线身份层</strong><small>{selectedCharacter?.fast_approved ? "已通过极速基准" : "需要极速路线基准批准"}</small></span><input type="checkbox" checked={selectedCharacter?.fast_route_enabled ?? false} disabled={!selectedCharacter?.fast_approved || Boolean(busyAction)} onChange={(event) => { void applySettings({ character_id: selectedCharacter!.character_id, fast_character_enabled: event.target.checked }, "character-fast-route"); }} /></label>
            <label className="rvc-setting-row"><span><strong>极速路线总开关</strong><small>仅处理已批准并启用的角色</small></span><input type="checkbox" checked={workspace?.settings.fast_route_enabled ?? false} disabled={!workspace?.characters.some((item) => item.fast_route_enabled && item.fast_approved) || Boolean(busyAction)} onChange={(event) => { void applySettings({ fast_route_enabled: event.target.checked }, "fast-route"); }} /></label>
            <label className="rvc-setting-row"><span><strong>质量稳定层</strong><small>{selectedCharacter?.quality_approved ? "已通过质量基准" : "需要质量路线基准批准"}</small></span><input type="checkbox" checked={selectedCharacter?.stability_enabled ?? false} disabled={!selectedCharacter?.quality_approved || Boolean(busyAction)} onChange={(event) => { void applySettings({ character_id: selectedCharacter!.character_id, stability_enabled: event.target.checked }, "character-stability"); }} /></label>
            <label className="rvc-setting-row"><span><strong>质量路线总开关</strong><small>仅处理已批准并启用的角色</small></span><input type="checkbox" checked={workspace?.settings.quality_stability_enabled ?? false} disabled={!workspace?.characters.some((item) => item.stability_enabled && item.quality_approved) || Boolean(busyAction)} onChange={(event) => { void applySettings({ quality_stability_enabled: event.target.checked }, "quality-stability"); }} /></label>
          </section>

          <section className="rvc-benchmark-section rvc-gate-section">
            <header><div><GitCompareArrows size={16} /><strong>路线基准与审批</strong></div><span>{activeBenchmark ? `${activeBenchmark.status} · ${activeBenchmark.decision}` : "尚未运行"}</span></header>
            <div className="rvc-preview-source" aria-label="RVC 基准路线">
              <button type="button" className={benchmarkRoute === "quality" ? "active" : ""} onClick={() => setBenchmarkRoute("quality")}>质量稳定层</button>
              <button type="button" className={benchmarkRoute === "fast" ? "active" : ""} onClick={() => setBenchmarkRoute("fast")}>极速身份层</button>
            </div>
            {profileDraft && (
              <div className="rvc-profile-editor">
                <label><span>音高偏移</span><input type="number" min={-24} max={24} value={profileDraft.f0_up_key} onChange={(event) => setProfileDraft({ ...profileDraft, preset: "custom", f0_up_key: Number(event.target.value) })} /></label>
                <label><span>索引混合</span><input type="number" min={0} max={1} step={0.05} value={profileDraft.index_rate} onChange={(event) => setProfileDraft({ ...profileDraft, preset: "custom", index_rate: Number(event.target.value) })} /></label>
                <label><span>响度混合</span><input type="number" min={0} max={1} step={0.05} value={profileDraft.rms_mix_rate} onChange={(event) => setProfileDraft({ ...profileDraft, preset: "custom", rms_mix_rate: Number(event.target.value) })} /></label>
                <label><span>辅音保护</span><input type="number" min={0} max={0.5} step={0.05} value={profileDraft.protect} onChange={(event) => setProfileDraft({ ...profileDraft, preset: "custom", protect: Number(event.target.value) })} /></label>
                <label><span>中值滤波</span><input type="number" min={0} max={7} value={profileDraft.filter_radius} onChange={(event) => setProfileDraft({ ...profileDraft, preset: "custom", filter_radius: Number(event.target.value) })} /></label>
                <label><span>输出采样率</span><select value={profileDraft.resample_sr} onChange={(event) => setProfileDraft({ ...profileDraft, preset: "custom", resample_sr: Number(event.target.value) })}><option value={0}>保持原始</option><option value={40000}>40 kHz</option><option value={48000}>48 kHz</option></select></label>
                <button className="secondary-button" disabled={Boolean(busyAction)} onClick={() => { void saveInferenceProfile(); }}><Save size={13} />保存路线参数</button>
                <small>修改参数会撤销该路线已有批准，避免未评测配置进入生产。</small>
              </div>
            )}
            <button
              className="primary-button rvc-preview-generate"
              disabled={!selectedCharacter || !selectedModel?.index_path || Boolean(busyAction) || activeBenchmark?.status === "queued" || activeBenchmark?.status === "running"}
              onClick={() => { void startBenchmark(); }}
            >{busyAction === "benchmark" || activeBenchmark?.status === "running" ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}{activeBenchmark?.status === "complete" ? "重新运行 24 句基准" : "运行 24 句基准"}</button>
            {activeBenchmark && (
              <>
                <div className="rvc-job-progress"><span style={{ width: `${activeBenchmark.progress}%` }} /></div>
                <p className="rvc-preview-hint">{activeBenchmark.message}</p>
                {activeBenchmark.error && <p className="rvc-feedback">{activeBenchmark.error}</p>}
                {activeBenchmark.samples.length > 0 && (
                  <div className="rvc-benchmark-samples">
                    {activeBenchmark.samples.map((sample, index) => (
                      <article key={sample.sample_id} className={sample.automatic_pass ? "passed" : "failed"}>
                        <header><span>{index + 1}. {sample.text}</span><b>{sample.automatic_pass ? "自动质检通过" : "检查退化"}</b></header>
                        <div><AudioPlayer src={sample.base_audio_url} label={`基线 ${index + 1}`} /><AudioPlayer src={sample.rvc_audio_url} label={`RVC ${index + 1}`} /></div>
                      </article>
                    ))}
                  </div>
                )}
                {activeBenchmark.status === "complete" && activeBenchmark.decision === "pending" && (
                  <div className="rvc-benchmark-review">
                    <label><span>RVC 盲听偏好</span><input type="number" min={0} max={100} value={preferencePercent} onChange={(event) => setPreferencePercent(Number(event.target.value))} /><b>%</b></label>
                    <label><input type="checkbox" checked={identityImproved} onChange={(event) => setIdentityImproved(event.target.checked)} />角色身份更稳定</label>
                    <label><input type="checkbox" checked={intelligibilityPreserved} onChange={(event) => setIntelligibilityPreserved(event.target.checked)} />可懂度没有退化</label>
                    <label><input type="checkbox" checked={expressionPreserved} onChange={(event) => setExpressionPreserved(event.target.checked)} />情绪与表现力保留</label>
                    <textarea maxLength={2000} placeholder="审核备注" value={reviewNotes} onChange={(event) => setReviewNotes(event.target.value)} />
                    <div><button className="stop-button" disabled={busyAction === "benchmark-review"} onClick={() => { void reviewBenchmark(false); }}>拒绝此路线</button><button className="primary-button" disabled={!activeBenchmark.automatic_pass || preferencePercent < 70 || !identityImproved || !intelligibilityPreserved || !expressionPreserved || busyAction === "benchmark-review"} onClick={() => { void reviewBenchmark(true); }}><CheckCircle2 size={14} />批准此路线</button></div>
                  </div>
                )}
              </>
            )}
          </section>

          <section className="rvc-benchmark-section rvc-preview-section">
            <header><div><GitCompareArrows size={16} /><strong>A/B 音色试听</strong></div><span>{previewResult ? "已生成" : "待生成"}</span></header>
            <div className="rvc-preview-source" aria-label="试听基线模型">
              <button type="button" disabled={busyAction === "preview"} className={previewSource === "voxcpm2" ? "active" : ""} aria-pressed={previewSource === "voxcpm2"} onClick={() => setPreviewSource("voxcpm2")}>VoxCPM2</button>
              <button type="button" disabled={busyAction === "preview"} className={previewSource === "gpt_sovits_v2_pro_plus" ? "active" : ""} aria-pressed={previewSource === "gpt_sovits_v2_pro_plus"} onClick={() => setPreviewSource("gpt_sovits_v2_pro_plus")}>GSV V2 Pro+</button>
            </div>
            <label className="rvc-preview-text"><span>试听文本</span><textarea disabled={busyAction === "preview"} maxLength={500} value={previewText} onChange={(event) => setPreviewText(event.target.value)} /></label>
            <button
              className="primary-button rvc-preview-generate"
              disabled={!selectedCharacter || !selectedModel?.index_path || !previewText.trim() || busyAction === "preview" || (previewSource === "gpt_sovits_v2_pro_plus" && !selectedCharacter.sample_audio_url)}
              onClick={() => { void generatePreview(); }}
            >{busyAction === "preview" ? <LoaderCircle className="spin" size={14} /> : <WandSparkles size={14} />}{busyAction === "preview" ? "生成 A/B 音频中" : "一键生成 A/B 试听"}</button>
            {!selectedModel?.index_path && <p className="rvc-preview-hint">先为当前角色绑定完整的 PTH + INDEX 模型。</p>}
            {previewSource === "gpt_sovits_v2_pro_plus" && !selectedCharacter?.sample_audio_url && <p className="rvc-preview-hint">GSV V2 Pro+ 需要当前角色参考音频。</p>}
            {previewFeedback && <p className="rvc-feedback">{previewFeedback}</p>}
            {previewResult && (
              <div className="rvc-preview-comparison">
                <article>
                  <header><span>基线</span><strong>{previewResult.source_label}</strong></header>
                  <Waveform src={previewResult.base_audio_url} color="gold" barCount={44} />
                  <AudioPlayer src={previewResult.base_audio_url} label={`${previewResult.source_label} 基线试听`} />
                </article>
                <article className="processed">
                  <header><span>RVC 后处理</span><strong>{selectedModel?.label ?? "当前模型"}</strong></header>
                  <Waveform src={previewResult.rvc_audio_url} color="violet" barCount={44} />
                  <AudioPlayer src={previewResult.rvc_audio_url} label="RVC 后处理试听" />
                </article>
              </div>
            )}
          </section>

          <section className="rvc-inventory-section">
            <header><div><Database size={16} /><strong>本地模型</strong></div><span>{workspace?.models.length ?? 0}</span></header>
            <div className="rvc-model-list">
              {workspace?.models.map((model) => (
                <button key={model.model_id} className={model.model_id === selectedCharacter?.selected_model_id ? "selected" : ""} onClick={() => selectedCharacter && applySettings({ character_id: selectedCharacter.character_id, selected_model_id: model.model_id }, "select-model")}>
                  <span><strong>{model.label}</strong><small>{model.source === "trained" ? "项目训练" : "现有资产"} · {model.size_mb} MB · {model.status}</small></span>
                  <b>{model.approved_routes.length ? model.approved_routes.join(" / ") : model.index_path ? "待基准" : "仅 PTH"}</b>
                </button>
              ))}
              {!workspace?.models.length && <p className="rvc-empty">未扫描到 RVC 模型资产</p>}
            </div>
          </section>
        </aside>
      </div>
    </section>
  );
}
