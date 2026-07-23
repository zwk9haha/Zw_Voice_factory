import { Check, FileText, ListChecks, Mic2, Settings2, SlidersHorizontal, Sparkles, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ProductionStage, ProductionStageId } from "./types";

const stageIcons: Record<ProductionStageId, LucideIcon> = {
  template: Settings2,
  source: FileText,
  casting: Users,
  references: Mic2,
  emotions: Sparkles,
  director: SlidersHorizontal,
  quality_render: ListChecks,
};

interface WorkflowNavProps {
  stages: ProductionStage[];
  activeStage: ProductionStageId;
  onStageChange: (stage: ProductionStageId) => void;
}

export function WorkflowNav({ stages, activeStage, onStageChange }: WorkflowNavProps) {
  return (
    <nav className="workflow-nav" aria-label="生产阶段">
      {stages.map((stage, index) => {
        const Icon = stageIcons[stage.stage_id];
        const isActive = activeStage === stage.stage_id;
        return (
          <button
            key={stage.stage_id}
            className={`${isActive ? "active" : ""} ${stage.status === "complete" ? "complete" : ""}`}
            onClick={() => onStageChange(stage.stage_id)}
          >
            <span className="stage-index">{stage.status === "complete" && !isActive ? <Check size={12} /> : index + 1}</span>
            <Icon size={14} />
            <span>{stage.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
