import clsx from "clsx";
import { IconShieldCheckFilled, IconShieldQuestion, IconRobot } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import { truncateHash } from "@/lib/format";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

export function CompleteSecurePanel({ evidence, highlighted }: { evidence: FullEvidenceObject; highlighted: boolean }) {
  const isComplete = evidence.final_status === "RESOLVED" && Boolean(evidence.signature);

  return (
    <Panel
      title="Sign-off"
      className={clsx("transition-shadow", highlighted && "ring-1 ring-amber/50 border-amber/40")}
      bodyClassName="flex flex-col gap-3 p-3"
    >
      <div
        className={clsx(
          "flex w-fit items-center gap-1.5 border px-2.5 py-1 font-data text-[10.5px] uppercase tracking-[0.08em]",
          isComplete ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-warning/10 text-warning"
        )}
      >
        {isComplete ? <IconShieldCheckFilled size={13} strokeWidth={1.5} /> : <IconShieldQuestion size={13} strokeWidth={1.5} />}
        {isComplete ? "Complete & Secure" : evidence.final_status}
      </div>

      {evidence.signature && (
        <div className="flex flex-col gap-2">
          <p className="text-[10px] uppercase tracking-[0.06em] text-text-dim">Signed by</p>
          <div className="flex items-center gap-2">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border-soft bg-white/[0.03] text-text-muted">
              <IconRobot size={13} strokeWidth={1.5} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11px] text-text">evidence-agent</p>
              <p className="truncate text-[9.5px] text-text-dim">service identity</p>
            </div>
            <span className="shrink-0 font-data text-[9.5px] text-text-dim" title={evidence.signature}>
              {truncateHash(evidence.signature)}
            </span>
          </div>
        </div>
      )}
    </Panel>
  );
}
