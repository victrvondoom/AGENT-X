"use client";

import clsx from "clsx";
import { Panel } from "../command-center/Panel";
import type { FindingOption } from "@/lib/sentinel/api";

const severityStyle: Record<FindingOption["severity"], { border: string; text: string }> = {
  critical: { border: "border-l-danger", text: "text-danger" },
  high: { border: "border-l-warning", text: "text-warning" },
  medium: { border: "border-l-neutral", text: "text-text-muted" },
  low: { border: "border-l-success", text: "text-success" },
};

interface FindingsPanelProps {
  options: FindingOption[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading?: boolean;
  error?: string | null;
}

export function FindingsPanel({ options, selectedId, onSelect, loading, error }: FindingsPanelProps) {
  return (
    <Panel title="Findings" bodyClassName="flex flex-col gap-2 overflow-auto p-2">
      {loading && options.length === 0 ? (
        <p className="px-2 py-4 text-center text-[11px] text-text-dim">connecting…</p>
      ) : error && options.length === 0 ? (
        <p className="px-2 py-4 text-center text-[11px] text-danger">{error}</p>
      ) : (
        options.map((finding) => {
          const tone = severityStyle[finding.severity];
          const isSelected = selectedId === finding.id;
          return (
            <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
              key={finding.id}
              type="button"
              onClick={() => onSelect(finding.id)}
              className={clsx(
                "flex w-full flex-col items-start gap-1 border-l-2 bg-black/20 px-3 py-2 text-left transition-colors",
                tone.border,
                isSelected ? "bg-amber-soft/40" : "hover:bg-white/[0.02]"
              )}
            >
              <div className="flex w-full items-center justify-between gap-2">
                <span className={clsx("text-[12px] font-medium", isSelected ? "text-amber" : "text-text")}>{finding.component}</span>
                <span className={clsx("shrink-0 font-data text-[9px] uppercase tracking-[0.06em]", tone.text)}>{finding.severity}</span>
              </div>
              <p className="font-data text-[10px] leading-snug text-text-muted">{finding.cve}</p>
            </button>
          );
        })
      )}
    </Panel>
  );
}
