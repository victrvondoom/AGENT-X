"use client";

import Link from "next/link";
import { Panel } from "../../command-center/Panel";
import { usePendingGateReviews } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";

/** Real pending sign-offs: sealed evidence with no Deployment Gate decision
 * yet - the exact same GET /api/deployment-gate/pending the Audit Ledger's
 * DWS Review Portal uses, so this list and that one never disagree. */
export function PendingSignOffsPanel() {
  const { pending, loading, error } = usePendingGateReviews();

  return (
    <Panel title="Pending Sign-offs" bodyClassName="flex flex-col p-1">
      {loading && pending.length === 0 && !error ? (
        <p className="px-3 py-3 text-[11px] text-text-dim">connecting…</p>
      ) : error ? (
        <p className="px-3 py-3 text-[11px] text-danger">{error}</p>
      ) : pending.length === 0 ? (
        <p className="px-3 py-3 text-[11px] text-text-dim">queue clear</p>
      ) : (
        pending.map((task) => (
          <Link
            prefetch={false}
            key={task.finding_id}
            href={`/deployment-gate?finding_id=${encodeURIComponent(task.finding_id)}`}
            className="flex items-start gap-2 border-b border-border-soft px-2 py-2 text-left transition-colors last:border-b-0 hover:bg-white/[0.02]"
          >
            <div className="min-w-0 flex-1">
              <p className="text-[11px] text-text">
                <span className="font-data text-amber">{task.finding_id}</span> — {task.title}
              </p>
              <p className="mt-0.5 text-[9.5px] text-text-dim">{formatTimestampUtc(task.submitted_at)}</p>
            </div>
          </Link>
        ))
      )}
    </Panel>
  );
}
