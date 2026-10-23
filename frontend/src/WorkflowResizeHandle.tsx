import { useRef, useState } from "react";

interface WorkflowResizeHandleProps {
  label: string;
  orientation: "horizontal" | "vertical";
  value: number;
  min: number;
  max: number;
  reverse?: boolean;
  controls: string;
  onChange: (value: number) => void;
  onReset: () => void;
}

export function WorkflowResizeHandle({ label, orientation, value, min, max, reverse, controls, onChange, onReset }: WorkflowResizeHandleProps) {
  const drag = useRef<{ pointerId: number; coordinate: number; value: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const sign = reverse ? -1 : 1;
  const coordinate = (event: { clientX: number; clientY: number }) => orientation === "vertical" ? event.clientX : event.clientY;
  const change = (next: number) => onChange(Math.round(Math.max(min, Math.min(max, next))));
  return <div
    className={`workflow-resize-handle workflow-resize-handle--${orientation}${dragging ? " is-dragging" : ""}`}
    role="separator" tabIndex={0} aria-label={label} aria-orientation={orientation} aria-controls={controls}
    aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-valuetext={`${value} 像素`}
    title={`${label}；双击恢复默认，方向键微调`}
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { pointerId: event.pointerId, coordinate: coordinate(event), value };
      setDragging(true);
    }}
    onPointerMove={(event) => {
      if (drag.current?.pointerId !== event.pointerId) return;
      event.preventDefault();
      change(drag.current.value + (coordinate(event) - drag.current.coordinate) * sign);
    }}
    onPointerUp={(event) => {
      if (drag.current?.pointerId !== event.pointerId) return;
      drag.current = null;
      setDragging(false);
      event.currentTarget.releasePointerCapture(event.pointerId);
    }}
    onLostPointerCapture={() => { drag.current = null; setDragging(false); }}
    onDoubleClick={onReset}
    onKeyDown={(event) => {
      const negative = orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
      const positive = orientation === "vertical" ? "ArrowRight" : "ArrowDown";
      if (![negative, positive, "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      change(event.key === "Home" ? min : event.key === "End" ? max : value + (event.key === positive ? 1 : -1) * sign * (event.shiftKey ? 48 : 16));
    }}
  ><span aria-hidden="true" /></div>;
}
