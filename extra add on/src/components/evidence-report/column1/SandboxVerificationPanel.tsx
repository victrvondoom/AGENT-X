import clsx from "clsx";
import Link from "next/link";
import { IconCircleCheck, IconAlertTriangle, IconArrowUpRight } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

export function SandboxVerificationPanel({ evidence, highlighted }: { evidence: FullEvidenceObject; highlighted: boolean }) {
  const results = evidence.verification_results;

  return (
    <Panel
      title="Isolated Sandbox Verification Results"
      className={clsx("transition-shadow", highlighted && "ring-1 ring-amber/50 border-amber/40")}
      bodyClassName="flex flex-col gap-2 p-3"
    >
      {results.length === 0 ? (
        <p className="text-[11px] text-text-dim">No sandbox scenarios recorded for this finding.</p>
      ) : (
        results.map((r) => {
          const resolved = r.result === "RESOLVED";
          return (
            <div key={r.sandbox_id} className="flex items-start gap-2 text-[11px] leading-snug text-text-muted">
              {resolved ? (
                <IconCircleCheck size={13} strokeWidth={1.5} className="mt-0.5 shrink-0 text-success" />
              ) : (
                <IconAlertTriangle size={13} strokeWidth={1.5} className="mt-0.5 shrink-0 text-danger" />
              )}
              <span>
                <span className="font-data text-text">sandbox {r.sandbox_id}</span> — {r.observed} ({r.duration_ms}ms)
              </span>
            </div>
          );
        })
      )}
      <Link
        prefetch={false}
        href={`/verification-lab?finding_id=${encodeURIComponent(evidence.finding_id)}`}
        className="mt-1 flex w-fit items-center gap-1 font-data text-[10px] text-amber hover:underline"
      >
        view in Verification Lab
        <IconArrowUpRight size={11} strokeWidth={1.5} />
      </Link>
    </Panel>
  );
}
