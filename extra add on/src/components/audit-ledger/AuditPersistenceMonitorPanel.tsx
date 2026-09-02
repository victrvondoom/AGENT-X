"use client";

import clsx from "clsx";
import { IconDatabase } from "@tabler/icons-react";
import { Panel } from "../command-center/Panel";
import { useHealth } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";

export function AuditPersistenceMonitorPanel() {
  const { health, loading, error } = useHealth();

  if (loading && !health) {
    return (
      <Panel title="Audit Persistence Monitor" bodyClassName="flex items-center justify-center p-3">
        <span className="text-[11px] text-text-dim">connecting…</span>
      </Panel>
    );
  }
  if (error && !health) {
    return (
      <Panel title="Audit Persistence Monitor" bodyClassName="flex items-center justify-center p-3">
        <span className="text-[11px] text-danger">{error}</span>
      </Panel>
    );
  }
  if (!health) return null;

  const { evidence_integrity_pct, evidence_count, evidence_verified_count, memory_bank, checked_at } = health;
  const collectionEntries = Object.entries(memory_bank.collections);

  return (
    <Panel title="Audit Persistence Monitor" bodyClassName="flex flex-col gap-3 p-3">
      <div>
        <div className="flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-[0.06em] text-text-dim">Evidence signature integrity</span>
          <span className="font-data text-[13px] tabular-nums text-success">{evidence_integrity_pct}%</span>
        </div>
        <div className="mt-1.5 h-1.5 w-full overflow-hidden bg-border-soft">
          <div className="h-full bg-success transition-[width] duration-500" style={{ width: `${evidence_integrity_pct}%` }} />
        </div>
        <p className="mt-1 font-data text-[9px] text-text-dim">
          {evidence_verified_count}/{evidence_count} sealed records re-verified against their stored signature
        </p>
      </div>

      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
          <IconDatabase size={13} strokeWidth={1.5} />
          Memory bank
        </span>
        <span
          className={clsx(
            "border px-1.5 py-0.5 font-data text-[9.5px] uppercase tracking-[0.06em]",
            memory_bank.healthy ? "border-success/40 bg-success/10 text-success" : "border-danger/40 bg-danger/10 text-danger"
          )}
        >
          {memory_bank.healthy ? "healthy" : "degraded"}
        </span>
      </div>
      {collectionEntries.length > 0 && (
        <div className="flex flex-col gap-0.5 font-data text-[9px] text-text-dim">
          {collectionEntries.map(([name, count]) => (
            <div key={name} className="flex justify-between">
              <span>{name}</span>
              <span className="text-text-muted">{count} docs</span>
            </div>
          ))}
        </div>
      )}

      <p className="font-data text-[9.5px] text-text-dim">last verified {formatTimestampUtc(checked_at)}</p>
    </Panel>
  );
}
