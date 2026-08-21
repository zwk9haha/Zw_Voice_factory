import { ChevronLeft, ChevronRight, LoaderCircle, RefreshCw, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ReferencePlanItem } from "./types";

interface ReferenceTextPanelProps {
  reference: ReferencePlanItem;
  disabled: boolean;
  generating: boolean;
  onGenerate: () => Promise<void>;
  onSave: (text: string) => Promise<void>;
  onActivate: (versionId: string) => Promise<void>;
  onDelete: (versionId: string) => Promise<void>;
}

const sourceLabel = {
  initial: "初始文本",
  generated: "本地模型",
  edited: "用户编辑",
} as const;

function formatCreatedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { hour12: false });
}

export function ReferenceTextPanel({
  reference,
  disabled,
  generating,
  onGenerate,
  onSave,
  onActivate,
  onDelete,
}: ReferenceTextPanelProps) {
  const [draft, setDraft] = useState(reference.reference_text);
  const versions = reference.reference_text_versions;
  const activeIndex = useMemo(() => {
    const index = versions.findIndex((version) => version.version_id === reference.active_reference_text_version_id);
    return index >= 0 ? index : Math.max(0, versions.length - 1);
  }, [reference.active_reference_text_version_id, versions]);
  const activeVersion = versions[activeIndex];

  useEffect(() => {
    setDraft(reference.reference_text);
  }, [reference.reference_id, reference.reference_text]);

  async function move(offset: number) {
    const version = versions[activeIndex + offset];
    if (version) await onActivate(version.version_id);
  }

  return (
    <section className="reference-text-panel" aria-label="标准参考文本版本管理">
      <div className="reference-text-heading">
        <div><span>标准参考文本</span><strong>{versions.length ? `${activeIndex + 1} / ${versions.length}` : "暂无版本"}</strong></div>
        <button className="secondary-button" disabled={disabled || generating} onClick={() => void onGenerate()}>
          {generating ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
          本地模型生成
        </button>
      </div>
      {activeVersion && (
        <div className="reference-version-nav" aria-label="标准参考文本版本切换">
          <button title="上一版文本" disabled={disabled || activeIndex === 0} onClick={() => void move(-1)}><ChevronLeft size={14} /></button>
          <div><strong>{sourceLabel[activeVersion.source]}</strong><span>{formatCreatedAt(activeVersion.created_at)}</span></div>
          <button title="下一版文本" disabled={disabled || activeIndex + 1 >= versions.length} onClick={() => void move(1)}><ChevronRight size={14} /></button>
          <button className="reference-version-delete" title="删除当前文本版本" disabled={disabled} onClick={() => void onDelete(activeVersion.version_id)}><Trash2 size={14} /></button>
        </div>
      )}
      <textarea
        aria-label="编辑标准参考文本"
        maxLength={180}
        value={draft}
        disabled={disabled}
        placeholder="生成或输入一条中性标准参考句"
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className="reference-text-actions">
        <small>{draft.length} / 180</small>
        <button className="secondary-button" disabled={disabled || !draft.trim() || draft.trim() === reference.reference_text} onClick={() => void onSave(draft.trim())}><Save size={14} />保存为新版本</button>
      </div>
    </section>
  );
}
