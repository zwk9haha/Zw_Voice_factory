import { CheckCircle2, CircleAlert, Layers3, LoaderCircle } from "lucide-react";
import type { ContinuousProductionRun, ProductionSliceState } from "./types";

interface ContinuousSliceNavigatorProps {
  run: ContinuousProductionRun;
  selectedSliceId: string | null;
  onSelect: (sliceId: string) => void;
}

const stateLabel: Record<ProductionSliceState["state"], string> = {
  pending: "待处理",
  analyzing: "扫描中",
  casting: "角色处理中",
  references: "参考处理中",
  emotions: "情绪处理中",
  directing: "导演处理中",
  render_ready: "可朗读",
  rendering: "生成中",
  playing: "播放中",
  complete: "已完成",
  blocked: "待处理",
  failed: "失败",
  skipped: "已跳过",
};

function SliceStateIcon({ slice }: { slice: ProductionSliceState }) {
  if (slice.state === "complete") return <CheckCircle2 size={12} />;
  if (slice.state === "failed" || slice.state === "blocked") return <CircleAlert size={12} />;
  if (["analyzing", "casting", "references", "emotions", "directing", "rendering"].includes(slice.state)) {
    return <LoaderCircle className="spin" size={12} />;
  }
  return <b>{slice.index}</b>;
}

export function ContinuousSliceNavigator({ run, selectedSliceId, onSelect }: ContinuousSliceNavigatorProps) {
  const selected = run.slices.find((slice) => slice.slice_id === selectedSliceId)
    ?? run.slices.find((slice) => slice.slice_id === run.current_slice_id)
    ?? run.slices[0]
    ?? null;
  const completed = run.slices.filter((slice) => slice.state === "complete").length;
  const ready = run.slices.filter((slice) => ["render_ready", "rendering", "playing"].includes(slice.state)).length;

  return (
    <section className="global-slice-navigator" aria-label="长篇生产切片">
      <header>
        <span><Layers3 size={14} /><strong>{selected ? "切片 " + selected.index + " · " + selected.title : "正在建立切片"}</strong></span>
        <div><span><b>{completed}</b>/{run.slices.length} 完成</span><span><b>{ready}</b> 可朗读</span><em>{run.message}</em></div>
      </header>
      <div className="global-slice-strip" role="list">
        {run.slices.map((slice) => (
          <button
            type="button"
            role="listitem"
            key={slice.slice_id}
            className={"state--" + slice.state + (slice.slice_id === selected?.slice_id ? " selected" : "")}
            title={slice.title + " · " + slice.message + (slice.error ? " · " + slice.error : "")}
            onClick={() => onSelect(slice.slice_id)}
          >
            <span><SliceStateIcon slice={slice} /><strong>切片 {slice.index}</strong><b>{slice.progress}%</b></span>
            <i><span style={{ width: slice.progress + "%" }} /></i>
            <small>
              {stateLabel[slice.state]}
              {slice.candidate_count > 0 ? " · 复用 " + slice.reused_character_count + " / 新增 " + slice.new_character_count : ""}
              {slice.director_total_passages > 0 ? " · 片内 " + slice.director_completed_passages + "/" + slice.director_total_passages : ""}
              {slice.segment_count > 0 ? " · " + slice.completed_segment_count + "/" + slice.segment_count + " 句" : ""}
            </small>
          </button>
        ))}
      </div>
    </section>
  );
}
