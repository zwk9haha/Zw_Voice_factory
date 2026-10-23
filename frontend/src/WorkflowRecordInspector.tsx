import { useState } from "react";
import { Check, Copy } from "lucide-react";

const PAGE_SIZE = 30;

function JsonValue({ label, value, depth = 0 }: { label: string; value: unknown; depth?: number }) {
  const [expanded, setExpanded] = useState(depth === 0);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [fullText, setFullText] = useState(false);
  if (value !== null && typeof value === "object") {
    const isArray = Array.isArray(value);
    const keys = isArray ? null : Object.keys(value);
    const count = isArray ? value.length : keys!.length;
    return <div className="workflow-json-branch">
      <button type="button" className="workflow-json-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span><b>{label}</b><small>{isArray ? `数组 · ${count} 项` : `对象 · ${count} 个字段`}</small>
      </button>
      {expanded && <div className="workflow-json-children">
        {depth >= 12 ? <span>内容层级较深，可复制完整 JSON 查看。</span> : <>
          {Array.from({ length: Math.min(limit, count) }, (_, index) => {
            const key = isArray ? index : keys![index];
            return <JsonValue key={key} label={String(key)} value={(value as Record<string | number, unknown>)[key]} depth={depth + 1} />;
          })}
          {count > limit && <button type="button" className="workflow-trace-text-button" onClick={() => setLimit(limit + PAGE_SIZE)}>再显示 {Math.min(PAGE_SIZE, count - limit)} 项（剩余 {count - limit}）</button>}
        </>}
        {count === 0 && <span>空{isArray ? "数组" : "对象"}</span>}
      </div>}
    </div>;
  }
  const text = typeof value === "string" ? value : String(value);
  return <div className="workflow-json-leaf"><b>{label}</b><span>{text.length > 1200 && !fullText ? `${text.slice(0, 1200)}…` : text}</span>{text.length > 1200 && <button type="button" className="workflow-trace-text-button" onClick={() => setFullText(!fullText)}>{fullText ? "收起长文本" : `展开全文（${text.length.toLocaleString()} 字符）`}</button>}</div>;
}

export function WorkflowRecordInspector({ label, value }: { label: string; value: unknown }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2) ?? "null");
      setCopied(true);
      setError("");
    } catch { setError("复制失败，请检查剪贴板权限"); }
  }
  return <section className="workflow-record-inspector"><header><strong>{label}</strong><button type="button" className="workflow-trace-text-button" onClick={() => void copy()}>{copied ? <Check size={12} /> : <Copy size={12} />}{copied ? "已复制" : "复制完整 JSON"}</button></header>{error && <p role="alert">{error}</p>}<JsonValue label={label} value={value} /></section>;
}
