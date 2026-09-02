import clsx from "clsx";
import { IconSearch, IconClipboardCheck, IconTools, IconShieldSearch, IconUserCircle } from "@tabler/icons-react";
import type { GateDecision } from "@/lib/gate-types";

const steps = [
  { id: "hunter", label: "Hunter", Icon: IconSearch },
  { id: "analyst", label: "Analyst", Icon: IconClipboardCheck },
  { id: "patch-forge", label: "Patch Forge", Icon: IconTools },
  { id: "verifier", label: "Verifier", Icon: IconShieldSearch },
  { id: "human", label: "You", Icon: IconUserCircle },
] as const;

export function ProvenancePipeline({ decision }: { decision: GateDecision }) {
  return (
    <div className="mb-5 flex items-center">
      {steps.map((step, i) => {
        const isHuman = step.id === "human";
        const isLast = i === steps.length - 1;
        const tone = isHuman
          ? decision === "approved"
            ? "border-success/60 bg-success/10 text-success"
            : decision === "rejected"
              ? "border-danger/60 bg-danger/10 text-danger"
              : "border-amber/60 bg-amber-soft text-amber"
          : "border-border text-text-dim";

        return (
          <div key={step.id} className="flex items-center" style={{ flex: isLast ? "0 0 auto" : "1 1 auto" }}>
            <div className="flex flex-col items-center gap-1">
              <span
                className={clsx(
                  "flex h-6 w-6 items-center justify-center rounded-full border transition-colors",
                  tone,
                  isHuman && decision === "pending" && "gate-pending-glow"
                )}
                title={step.label}
              >
                <step.Icon size={12} strokeWidth={1.6} />
              </span>
              <span className="font-data text-[7.5px] uppercase tracking-[0.05em] text-text-dim">{step.label}</span>
            </div>
            {!isLast && <div className="mx-1.5 h-px flex-1 bg-border-soft" />}
          </div>
        );
      })}
    </div>
  );
}
