import { IconLock, IconCircleCheck, IconClockHour4 } from "@tabler/icons-react";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

interface EvidenceHeaderProps {
  evidence: FullEvidenceObject | null;
  findingId: string | null;
}

export function EvidenceHeader({ evidence, findingId }: EvidenceHeaderProps) {
  const isFinal = evidence?.final_status === "RESOLVED";
  const isSealed = Boolean(evidence?.signature);

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-soft px-4 py-3">
      <h1 className="text-[14px] font-medium text-text">Evidence Final Report</h1>
      <span className="font-data text-[11px] text-text-muted">{findingId ?? "no finding selected"}</span>
      {evidence?.final_status && <span className="font-data text-[10px] text-text-dim">{evidence.final_status}</span>}

      <div className="ml-auto flex items-center gap-2">
        <span
          className={`flex items-center gap-1.5 border px-2 py-1 font-data text-[10px] uppercase tracking-[0.08em] ${
            isFinal ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-warning/10 text-warning"
          }`}
        >
          {isFinal ? <IconCircleCheck size={12} strokeWidth={1.5} /> : <IconClockHour4 size={12} strokeWidth={1.5} />}
          {isFinal ? "Final" : evidence?.final_status ?? "Pending"}
        </span>
        <span
          className={`flex items-center gap-1.5 border px-2 py-1 font-data text-[10px] uppercase tracking-[0.08em] ${
            isSealed ? "border-border-soft bg-white/[0.03] text-text-muted" : "border-border-soft bg-white/[0.02] text-text-dim"
          }`}
        >
          <IconLock size={12} strokeWidth={1.5} />
          {isSealed ? "Sealed" : "Unsigned"}
        </span>
      </div>
    </div>
  );
}
