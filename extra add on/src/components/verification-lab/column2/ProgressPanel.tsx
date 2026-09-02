import clsx from "clsx";
import { IconCircle, IconLoader2, IconCircleCheck } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { ReplayStep } from "@/lib/types";

const statusIcon: Record<ReplayStep["status"], typeof IconCircle> = {
  pending: IconCircle,
  active: IconLoader2,
  done: IconCircleCheck,
};

const statusColor: Record<ReplayStep["status"], string> = {
  pending: "text-text-dim",
  active: "text-amber",
  done: "text-success",
};

function StageRow({ step }: { step: ReplayStep }) {
  const Icon = statusIcon[step.status];
  return (
    <div className="flex items-center gap-2 border-b border-border-soft px-3 py-2.5 last:border-b-0">
      <Icon size={14} strokeWidth={1.5} className={clsx(statusColor[step.status], step.status === "active" && "animate-spin")} />
      <span className="flex-1 text-[11.5px] capitalize text-text">{step.label}</span>
      <span className="font-data text-[9.5px] text-text-dim">{step.ts}</span>
    </div>
  );
}

export function ProgressPanel({ steps }: { steps: ReplayStep[] }) {
  return (
    <Panel title="Progress" bodyClassName="p-0">
      {steps.map((s) => (
        <StageRow key={s.id} step={s} />
      ))}
    </Panel>
  );
}
