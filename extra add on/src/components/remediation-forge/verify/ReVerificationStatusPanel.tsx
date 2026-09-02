import clsx from "clsx";
import { IconCheck, IconLoader2 } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { VerificationResultRecord } from "@/lib/sentinel/api";

const barColor: Record<VerificationResultRecord["result"], string> = {
  CONFIRMED_EXPLOITABLE: "bg-danger",
  RESOLVED: "bg-success",
  INCONCLUSIVE: "bg-warning",
};

const nodeStyle: Record<VerificationResultRecord["result"], string> = {
  CONFIRMED_EXPLOITABLE: "border-danger text-danger",
  RESOLVED: "border-success text-success",
  INCONCLUSIVE: "border-warning text-warning",
};

interface ReVerificationStatusPanelProps {
  results: VerificationResultRecord[];
  running: boolean;
  failed: boolean;
}

export function ReVerificationStatusPanel({ results, running, failed }: ReVerificationStatusPanelProps) {
  const allResolved = results.length > 0 && results[results.length - 1].result === "RESOLVED";

  return (
    <Panel
      title="Re-verification Status"
      headerRight={
        <span className={clsx("flex items-center gap-1.5 font-data text-[10px] uppercase tracking-[0.06em]", allResolved ? "text-success" : running ? "text-amber" : "text-text-dim")}>
          {running && <IconLoader2 size={11} strokeWidth={1.5} className="animate-spin" />}
          {running ? "executing sandbox scenarios" : allResolved ? "all assertions resolved" : failed ? "investigation failed" : "no results yet"}
        </span>
      }
      bodyClassName="flex flex-col gap-3 p-3"
    >
      {results.length === 0 && !running ? (
        <p className="text-[11px] text-text-dim">No sandbox scenarios have run yet.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {results.map((r) => (
            <div key={r.sandbox_id}>
              <div className="flex items-center justify-between font-data text-[10px] text-text-muted">
                <span className="truncate" title={r.scenario}>
                  {r.scenario}
                </span>
                <span className="shrink-0 tabular-nums">{r.duration_ms}ms</span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden bg-border-soft">
                <div className={clsx("h-full", barColor[r.result])} style={{ width: "100%" }} />
              </div>
            </div>
          ))}
          {running && (
            <div className="flex items-center gap-2 font-data text-[10px] text-amber">
              <IconLoader2 size={11} strokeWidth={1.5} className="animate-spin" />
              running next scenario…
            </div>
          )}
        </div>
      )}

      {results.length > 0 && (
        <div className="flex items-center justify-center gap-4 border-t border-border-soft pt-3">
          <div className="flex flex-col gap-2">
            {results.map((r) => (
              <div key={r.sandbox_id} className={clsx("border px-2 py-1 font-data text-[9.5px]", nodeStyle[r.result])}>
                {r.sandbox_id}
              </div>
            ))}
          </div>
          <svg width="28" height="52" className="shrink-0 text-border">
            <line x1="0" y1="10" x2="28" y2="26" stroke="currentColor" strokeWidth="1" />
            <line x1="0" y1="42" x2="28" y2="26" stroke="currentColor" strokeWidth="1" />
          </svg>
          <div
            className={clsx(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors",
              allResolved ? "border-success bg-success/10 text-success" : "border-border-soft text-text-dim"
            )}
          >
            <IconCheck size={16} strokeWidth={2} />
          </div>
        </div>
      )}
    </Panel>
  );
}
