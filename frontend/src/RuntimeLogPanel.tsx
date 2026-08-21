import { useQuery } from "@tanstack/react-query";
import { Download, FileText, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchRuntimeLog, fetchRuntimeLogs } from "./api";

interface RuntimeLogPanelProps {
  open: boolean;
  onClose: () => void;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function RuntimeLogPanel({ open, onClose }: RuntimeLogPanelProps) {
  const [selectedLogId, setSelectedLogId] = useState("");
  const logsQuery = useQuery({
    queryKey: ["runtime-logs"],
    queryFn: fetchRuntimeLogs,
    enabled: open,
    refetchInterval: open ? 5_000 : false,
    retry: false,
  });
  const contentQuery = useQuery({
    queryKey: ["runtime-log", selectedLogId],
    queryFn: () => fetchRuntimeLog(selectedLogId),
    enabled: open && Boolean(selectedLogId),
    refetchInterval: open && Boolean(selectedLogId) ? 5_000 : false,
    retry: false,
  });

  useEffect(() => {
    const logs = logsQuery.data ?? [];
    const first = logs[0]?.log_id;
    if (first && (!selectedLogId || !logs.some((item) => item.log_id === selectedLogId))) {
      setSelectedLogId(first);
    }
  }, [logsQuery.data, selectedLogId]);

  if (!open) return null;

  return (
    <section className="runtime-log-panel" aria-label="运行日志">
      <header className="runtime-log-panel__header">
        <div><FileText size={18} /><span><small>PROJECT DIAGNOSTICS</small><strong>运行日志</strong></span></div>
        <div className="runtime-log-panel__actions">
          <button className="icon-button" title="刷新日志" onClick={() => { void logsQuery.refetch(); }}><RefreshCw size={15} /></button>
          <a className="secondary-button" href="/api/logs/export" download><Download size={14} />导出诊断包</a>
          <button className="icon-button" title="关闭日志" onClick={onClose}><X size={18} /></button>
        </div>
      </header>
      <div className="runtime-log-panel__body">
        <aside className="runtime-log-list">
          {logsQuery.data?.map((log) => (
            <button key={log.log_id} className={log.log_id === selectedLogId ? "selected" : ""} onClick={() => setSelectedLogId(log.log_id)}>
              <span><strong>{log.name}</strong><small>{log.category} · {formatBytes(log.size_bytes)}</small></span>
              <time dateTime={log.updated_at}>{new Date(log.updated_at).toLocaleTimeString()}</time>
            </button>
          ))}
          {!logsQuery.data?.length && <p>暂无运行日志</p>}
        </aside>
        <pre className="runtime-log-content">{contentQuery.isFetching ? "正在读取日志..." : contentQuery.data || "请选择日志文件"}</pre>
      </div>
    </section>
  );
}
