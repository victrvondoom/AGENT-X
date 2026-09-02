"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { IconLock, IconEye } from "@tabler/icons-react";
import { Panel } from "../command-center/Panel";
import { usePendingGateReviews } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";

/**
 * Real review queue: sealed evidence records with no Deployment Gate
 * decision yet, straight from GET /api/deployment-gate/pending - no
 * separate "review task" storage, and clicking through actually navigates
 * to the real Deployment Gate for that finding rather than just hiding the
 * row locally with no backend effect.
 */
export function DwsReviewPortalPanel() {
  const { pending, loading, error } = usePendingGateReviews();

  return (
    <Panel
      title="DWS Review Portal"
      headerRight={
        <span className="border border-warning/40 bg-warning/10 px-1.5 py-0.5 font-data text-[10px] text-warning">
          {pending.length} pending
        </span>
      }
      bodyClassName="p-1"
    >
      {loading && pending.length === 0 && !error ? (
        <p className="px-3 py-4 text-center text-[11px] text-text-dim">connecting…</p>
      ) : error ? (
        <p className="px-3 py-4 text-center text-[11px] text-danger">{error}</p>
      ) : (
        <AnimatePresence initial={false}>
          {pending.length === 0 && <p className="px-3 py-4 text-center text-[11px] text-text-dim">queue clear</p>}
          {pending.map((task) => (
            <motion.div
              key={task.finding_id}
              layout
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, x: 24, height: 0, marginTop: 0, marginBottom: 0 }}
              transition={{ duration: 0.25 }}
              className="flex items-center gap-2 border-b border-border-soft px-2 py-2 last:border-b-0"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[10.5px] text-text">{task.title}</p>
                <p className="font-data text-[9px] text-text-dim">{formatTimestampUtc(task.submitted_at)}</p>
              </div>
              {task.sealed && <IconLock size={11} strokeWidth={1.5} className="shrink-0 text-text-dim" />}
              <Link
                prefetch={false}
                href={`/deployment-gate?finding_id=${encodeURIComponent(task.finding_id)}`}
                className="flex shrink-0 items-center gap-1 border border-border-soft px-1.5 py-1 font-data text-[9.5px] uppercase tracking-[0.05em] text-text-muted transition-colors hover:border-amber/50 hover:text-amber"
              >
                <IconEye size={11} strokeWidth={1.5} />
                review
              </Link>
            </motion.div>
          ))}
        </AnimatePresence>
      )}
    </Panel>
  );
}
