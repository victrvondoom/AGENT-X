import clsx from "clsx";
import { IconSquareCheck, IconSquare, IconLock, IconClockHour4 } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import { truncateHash } from "@/lib/format";
import type { FullEvidenceObject, RelevanceVerdict } from "@/lib/sentinel/api";

interface ClaimVsEvidencePanelProps {
  verdict: RelevanceVerdict | null;
  evidence: FullEvidenceObject | null;
}

export function ClaimVsEvidencePanel({ verdict, evidence }: ClaimVsEvidencePanelProps) {
  const sealed = Boolean(evidence?.signature);
  const claims = verdict?.claims ?? [];

  return (
    <Panel title="Remediation Claim vs Evidence" bodyClassName="flex flex-col gap-3 p-3">
      {claims.length === 0 ? (
        <p className="text-[11px] text-text-dim">No sourced claims recorded yet.</p>
      ) : (
        <div className="flex flex-col gap-2.5 overflow-auto">
          {claims.map((c, i) => {
            const Icon = sealed ? IconSquareCheck : IconSquare;
            return (
              <div key={i} className="flex gap-2">
                <Icon size={14} strokeWidth={1.5} className={clsx("mt-0.5 shrink-0", sealed ? "text-success" : "text-text-dim")} />
                <div className="text-[11px] leading-snug">
                  <p className={sealed ? "text-text" : "text-text-muted"}>Claim: {c.statement}</p>
                  <p className={clsx("mt-0.5 font-data text-[10px]", sealed ? "text-success" : "text-text-dim")}>
                    {sealed ? "confirmed — " : "pending — "}
                    {c.source}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="border-t border-border-soft pt-3">
        <p className="text-[10px] uppercase tracking-[0.06em] text-text-dim">Audit log entry</p>
        <p className="mt-1 font-data text-[11px] text-text">{evidence?.finding_id ?? "no evidence yet"}</p>
        <div className="mt-1 flex items-center justify-between">
          <span className="truncate font-data text-[10px] text-text-dim" title={evidence?.signature ?? ""}>
            {evidence?.signature ? truncateHash(evidence.signature) : "unsigned"}
          </span>
          <span
            className={clsx(
              "ml-2 flex shrink-0 items-center gap-1 border px-1.5 py-0.5 font-data text-[9.5px] uppercase tracking-[0.06em]",
              sealed ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-warning/10 text-warning"
            )}
          >
            {sealed ? <IconLock size={10} strokeWidth={1.5} /> : <IconClockHour4 size={10} strokeWidth={1.5} />}
            {sealed ? "sealed" : "pending"}
          </span>
        </div>
      </div>
    </Panel>
  );
}
