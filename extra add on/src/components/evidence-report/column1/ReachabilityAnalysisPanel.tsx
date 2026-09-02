import clsx from "clsx";
import Link from "next/link";
import { IconCircleCheck, IconArrowUpRight } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

export function ReachabilityAnalysisPanel({ evidence, highlighted }: { evidence: FullEvidenceObject; highlighted: boolean }) {
  const claims = evidence.verdict?.claims ?? [];

  return (
    <Panel
      title="Reachability Analysis"
      className={clsx("transition-shadow", highlighted && "ring-1 ring-amber/50 border-amber/40")}
      bodyClassName="flex flex-col gap-2 p-3"
    >
      {claims.length === 0 ? (
        <p className="text-[11px] text-text-dim">No sourced claims recorded for this finding.</p>
      ) : (
        claims.map((claim, i) => (
          <div key={i} className="flex items-start gap-2 text-[11px] leading-snug text-text-muted">
            <IconCircleCheck size={13} strokeWidth={1.5} className="mt-0.5 shrink-0 text-success" />
            <span>
              {claim.statement}
              <span className="ml-1.5 font-data text-[9.5px] text-text-dim">[{claim.source}]</span>
            </span>
          </div>
        ))
      )}
      <Link
        prefetch={false}
        href={`/remediation?finding_id=${encodeURIComponent(evidence.finding_id)}`}
        className="mt-1 flex w-fit items-center gap-1 font-data text-[10px] text-amber hover:underline"
      >
        view call-chain in Remediation Forge
        <IconArrowUpRight size={11} strokeWidth={1.5} />
      </Link>
    </Panel>
  );
}
