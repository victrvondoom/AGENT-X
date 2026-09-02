"use client";

import { useState } from "react";
import clsx from "clsx";
import { IconChevronRight, IconCircleCheck, IconAlertTriangle } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

export function ReVerificationResultsPanel({ evidence, highlighted }: { evidence: FullEvidenceObject; highlighted: boolean }) {
  const [isOpen, setIsOpen] = useState(true);
  const results = evidence.verification_results;
  const passed = results.filter((r) => r.result === "RESOLVED").length;

  return (
    <Panel
      title="Re-verification Results (Verifier)"
      className={clsx("transition-shadow", highlighted && "ring-1 ring-amber/50 border-amber/40")}
      bodyClassName="flex flex-col p-1"
    >
      {results.length === 0 ? (
        <p className="px-3 py-4 text-center text-[11px] text-text-dim">No verification scenarios recorded yet.</p>
      ) : (
        <div className="border-b border-border-soft last:border-b-0">
          <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            type="button"
            onClick={() => setIsOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-2 py-2 text-left transition-colors hover:bg-white/[0.02]"
          >
            <IconChevronRight size={13} strokeWidth={1.5} className={clsx("shrink-0 text-text-dim transition-transform", isOpen && "rotate-90")} />
            <IconCircleCheck size={13} strokeWidth={1.5} className="shrink-0 text-success" />
            <span className="flex-1 text-[11.5px] text-text">Sandbox scenarios</span>
            <span className="font-data text-[10.5px] tabular-nums text-success">
              {passed}/{results.length} resolved
            </span>
          </button>
          {isOpen && (
            <div className="flex flex-col gap-1.5 py-1 pl-8 pr-2">
              {results.map((r) => (
                <div key={r.sandbox_id} className="flex items-start gap-1.5 font-data text-[10px] text-text-muted">
                  {r.result === "RESOLVED" ? (
                    <IconCircleCheck size={10} strokeWidth={1.5} className="mt-0.5 shrink-0 text-success/70" />
                  ) : (
                    <IconAlertTriangle size={10} strokeWidth={1.5} className="mt-0.5 shrink-0 text-danger/70" />
                  )}
                  <span>
                    {r.scenario} — {r.result} ({r.duration_ms}ms)
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
