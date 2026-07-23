import { ArrowRight, Check, CircleAlert, FileText, Gauge, Mic2, Play, RefreshCw, SlidersHorizontal, Sparkles, Upload, Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { InferenceTemplate, ProductionStageId, WorkspacePayload } from "./types";
import { ProjectPreparationWorkspace } from "./ProjectPreparationWorkspace";
import { Waveform } from "./Waveform";

type PreparationStageId = Exclude<ProductionStageId, "quality_render">;

interface PreparationWorkspaceProps {
  activeStage: PreparationStageId;
  selectedTemplate: InferenceTemplate;
  workspace: WorkspacePayload;
  onTemplateChange: (templateId: string) => void;
  onStageChange: (stage: ProductionStageId) => void;
}

const nextStage: Record<PreparationStageId, ProductionStageId> = {
  template: "source",
  source: "casting",
  casting: "references",
  references: "emotions",
  emotions: "director",
  director: "quality_render",
};

const stageData: Record<Exclude<PreparationStageId, "template">, {
  eyebrow: string;
  title: string;
  action: string;
  items: Array<{ title: string; meta: string; status: string }>;
  fields: Array<{ label: string; value: string }>;
  checks: Array<{ label: string; value: string; attention?: boolean }>;
}> = {
  source: {
    eyebrow: "SOURCE TEXT",
    title: "小说导入",
    action: "进入角色审核",
    items: [
      { title: "斗破苍穹天蚕土豆.txt", meta: "10.1 MB · UTF-8", status: "已解析" },
      { title: "斗破苍穹测试.txt", meta: "64.5 KB · UTF-8", status: "验收文本" },
    ],
    fields: [
      { label: "章节识别", value: "1,641 章" },
      { label: "正文字符", value: "4,286,392" },
      { label: "切句策略", value: "有声书长篇" },
      { label: "预计片段", value: "126,840 句" },
    ],
    checks: [
      { label: "编码检查", value: "通过" },
      { label: "章节边界", value: "通过" },
      { label: "异常字符", value: "17 处待确认", attention: true },
    ],
  },
  casting: {
    eyebrow: "CAST AUDIT",
    title: "角色候选审核",
    action: "进入标准参考",
    items: [
      { title: "萧炎", meta: "对话 386 · 提及 1,204", status: "核心角色" },
      { title: "旁白", meta: "叙事 8,492 段", status: "核心角色" },
      { title: "测验员", meta: "对话 12 · 提及 8", status: "配角" },
      { title: "想要知", meta: "误切短语 · 证据不足", status: "已驳回" },
    ],
    fields: [
      { label: "当前候选", value: "萧炎" },
      { label: "合并别名", value: "岩枭、萧族少年" },
      { label: "识别置信度", value: "97%" },
      { label: "角色权重", value: "94 · core" },
    ],
    checks: [
      { label: "直接对话证据", value: "386 条" },
      { label: "别名冲突", value: "0" },
      { label: "待人工确认", value: "3 个候选", attention: true },
    ],
  },
  references: {
    eyebrow: "CANONICAL REFERENCES",
    title: "中性标准参考",
    action: "进入情绪派生",
    items: [
      { title: "萧炎", meta: "VoxCPM2 · neutral v3", status: "已确认" },
      { title: "旁白", meta: "VoxCPM2 · neutral v2", status: "已确认" },
      { title: "测验员", meta: "VoxCPM2 · neutral v1", status: "待审核" },
    ],
    fields: [
      { label: "生成后端", value: "VoxCPM2" },
      { label: "固定语料", value: "CN-PHONEME-04" },
      { label: "随机种子", value: "42017" },
      { label: "参考时长", value: "10.2 秒" },
    ],
    checks: [
      { label: "已确认角色", value: "2 / 3" },
      { label: "响度一致性", value: "-18.1 LUFS" },
      { label: "待重生成", value: "1 条", attention: true },
    ],
  },
  emotions: {
    eyebrow: "EMOTION VARIANTS",
    title: "情绪声线派生",
    action: "进入导演脚本",
    items: [
      { title: "自然", meta: "parent: xiao_yan_neutral_v3", status: "已确认" },
      { title: "愤怒", meta: "intensity 0.72", status: "已确认" },
      { title: "悲伤", meta: "intensity 0.58", status: "已确认" },
      { title: "紧张", meta: "intensity 0.64", status: "待审核" },
      { title: "激动", meta: "intensity 0.81", status: "待生成" },
    ],
    fields: [
      { label: "父参考", value: "萧炎 · neutral v3" },
      { label: "比较文本", value: "CN-IDENTITY-02" },
      { label: "派生后端", value: "VoxCPM2" },
      { label: "身份相似度", value: "0.91" },
    ],
    checks: [
      { label: "已确认情绪", value: "3 / 5" },
      { label: "中性回退", value: "已启用" },
      { label: "待审核", value: "1 条", attention: true },
    ],
  },
  director: {
    eyebrow: "DIRECTOR DOCUMENT",
    title: "逐句导演",
    action: "进入质量渲染",
    items: [
      { title: "001 · 旁白", meta: "紧张 · 语速 0.94", status: "已编排" },
      { title: "002 · 测验员", meta: "冷淡 · 语速 0.88", status: "已编排" },
      { title: "003 · 萧炎", meta: "克制 · 语速 0.92", status: "已编排" },
    ],
    fields: [
      { label: "情绪", value: "克制" },
      { label: "强度", value: "0.62" },
      { label: "句前 / 句后", value: "120 / 260 ms" },
      { label: "速度 / 音高", value: "0.92 / 0.98" },
    ],
    checks: [
      { label: "角色绑定", value: "500 / 500" },
      { label: "情绪参考命中", value: "92%" },
      { label: "中性回退", value: "41 句", attention: true },
    ],
  },
};

function TemplateWorkspace({ selectedTemplate, workspace, onTemplateChange, onStageChange }: Omit<PreparationWorkspaceProps, "activeStage">) {
  const analysisLabels = { balanced: "平衡识别", character_recall: "角色召回优先", precision_first: "精度优先" };
  const segmentationLabels = { audiobook: "有声书", dialogue_dense: "对话密集", long_form: "长篇稳态" };
  const referenceLabels = { phoneme_coverage: "中文音素覆盖", emotion_contrast: "情绪对照" };

  return (
    <section className="prep-workspace">
      <aside className="prep-list-pane">
        <div className="pane-heading"><div><span className="eyebrow">INFERENCE TEMPLATES</span><h2>推理模板</h2></div><span className="list-count">{workspace.available_templates.length}</span></div>
        <div className="prep-list">
          {workspace.available_templates.map((template) => (
            <button key={template.template_id} className={template.template_id === selectedTemplate.template_id ? "selected" : ""} onClick={() => onTemplateChange(template.template_id)}>
              <Gauge size={16} />
              <span><strong>{template.display_name}</strong><small>{analysisLabels[template.analysis_profile]}</small></span>
              {template.template_id === selectedTemplate.template_id && <Check size={14} />}
            </button>
          ))}
        </div>
      </aside>

      <section className="prep-main-pane">
        <div className="prep-titlebar"><div><span className="eyebrow">PROJECT PRESET</span><h2>{selectedTemplate.display_name}</h2></div><button className="secondary-button" onClick={() => onTemplateChange(workspace.active_template.template_id)}><RefreshCw size={14} />恢复默认</button></div>
        <div className="config-grid">
          <div><span>角色分析</span><strong>{analysisLabels[selectedTemplate.analysis_profile]}</strong></div>
          <div><span>文本切句</span><strong>{segmentationLabels[selectedTemplate.segmentation_profile]}</strong></div>
          <div><span>参考语料</span><strong>{referenceLabels[selectedTemplate.reference_text_profile]}</strong></div>
        </div>
        <div className="model-role-table">
          <div className="model-role-head"><span>职责</span><span>模型</span><span>执行阶段</span></div>
          <div><span>中性参考生产</span><strong>VoxCPM2</strong><small>标准参考 / 情绪派生</small></div>
          <div><span>质量在线渲染</span><strong>GPT-SoVITS</strong><small>逐句生成 / 缓存</small></div>
          <div><span>音色稳定层</span><strong>RVC</strong><small>按角色 A/B 测评启用</small></div>
        </div>
      </section>

      <aside className="prep-check-pane">
        <div className="pane-heading"><div><span className="eyebrow">MODEL READINESS</span><h2>资源检查</h2></div></div>
        <div className="readiness-list">
          <div><Mic2 size={15} /><span><strong>VoxCPM2</strong><small>参考生产</small></span><b>可用</b></div>
          <div><Sparkles size={15} /><span><strong>GPT-SoVITS</strong><small>质量渲染</small></span><b>可用</b></div>
          <div><SlidersHorizontal size={15} /><span><strong>RVC</strong><small>稳定层</small></span><b>待测评</b></div>
        </div>
        <div className="stage-action"><button className="primary-button" onClick={() => onStageChange("source")}>应用模板<ArrowRight size={15} /></button></div>
      </aside>
    </section>
  );
}

type GenericPreparationWorkspaceProps = Omit<PreparationWorkspaceProps, "activeStage"> & {
  activeStage: Exclude<PreparationStageId, "template">;
};

function GenericPreparationWorkspace(props: GenericPreparationWorkspaceProps) {
  const data = stageData[props.activeStage];
  const [selectedIndex, setSelectedIndex] = useState(0);
  useEffect(() => setSelectedIndex(0), [props.activeStage]);
  const selectedItem = data.items[selectedIndex] ?? data.items[0];
  const selectedFields = props.activeStage === "source" && selectedIndex === 1
    ? [
        { label: "文本类型", value: "验收文本" },
        { label: "正文字符", value: "24,861" },
        { label: "目标章节", value: "第 1 章 · 测试段" },
        { label: "预计片段", value: "500 句" },
      ]
    : data.fields;
  const stageIcon = props.activeStage === "source" ? FileText : props.activeStage === "casting" ? Users : props.activeStage === "references" ? Mic2 : props.activeStage === "emotions" ? Sparkles : SlidersHorizontal;
  const StageIcon = stageIcon;
  const previewCharacter = props.workspace.characters[1];

  return (
    <section className="prep-workspace">
      <aside className="prep-list-pane">
        <div className="pane-heading"><div><span className="eyebrow">{data.eyebrow}</span><h2>{data.title}</h2></div><span className="list-count">{data.items.length}</span></div>
        <div className="prep-list">
          {data.items.map((item, index) => (
            <button key={item.title} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <StageIcon size={16} />
              <span><strong>{item.title}</strong><small>{item.meta}</small></span>
              <em>{item.status}</em>
            </button>
          ))}
        </div>
        {props.activeStage === "source" && <button className="import-button"><Upload size={15} />导入 TXT</button>}
      </aside>

      <section className="prep-main-pane">
        <div className="prep-titlebar"><div><span className="eyebrow">CURRENT SELECTION</span><h2>{selectedItem.title}</h2></div><button className="icon-button" title="刷新"><RefreshCw size={15} /></button></div>
        {props.activeStage === "references" && <div className="stage-wave"><Waveform color={previewCharacter.color} /><audio className="stage-audio" controls preload="metadata" src={previewCharacter.preview_audio_url ?? undefined} aria-label="标准参考试听" /></div>}
        <div className="field-table">
          {selectedFields.map((field) => <div key={field.label}><span>{field.label}</span><strong>{field.value}</strong></div>)}
        </div>
        {props.activeStage === "casting" && <div className="evidence-block"><span>证据摘录</span><p>“萧炎，斗之力，三段。级别，低级。”</p><p>“三十年河东，三十年河西，莫欺少年穷。”</p></div>}
      </section>

      <aside className="prep-check-pane">
        <div className="pane-heading"><div><span className="eyebrow">STAGE GATE</span><h2>阶段检查</h2></div></div>
        <div className="checkpoint-list">
          {data.checks.map((check) => <div key={check.label} className={check.attention ? "attention" : ""}>{check.attention ? <CircleAlert size={15} /> : <Check size={15} />}<span>{check.label}</span><strong>{check.value}</strong></div>)}
        </div>
        <div className="stage-action"><button className="primary-button" onClick={() => props.onStageChange(nextStage[props.activeStage])}>{data.action}<ArrowRight size={15} /></button></div>
      </aside>
    </section>
  );
}

export function PreparationWorkspace(props: PreparationWorkspaceProps) {
  const activeStage = props.activeStage;
  if (activeStage === "template") {
    return <TemplateWorkspace {...props} />;
  }
  if (activeStage === "source" || activeStage === "casting" || activeStage === "director") {
    return <ProjectPreparationWorkspace activeStage={activeStage} onStageChange={props.onStageChange} />;
  }
  return <GenericPreparationWorkspace {...props} activeStage={activeStage} />;
}
