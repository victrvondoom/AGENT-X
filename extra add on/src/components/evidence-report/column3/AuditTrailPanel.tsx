import clsx from "clsx";
import Link from "next/link";
import { IconArrowUpRight } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import { formatTimestampUtc } from "@/lib/format";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

interface AuditTrailPanelProps {
  evidence: FullEvidenceObject;
  selectedTarget: string | null;
  onSelect: (target: string) => void;
}

const HIGHLIGHT_TARGET: Record<string, string> = {
  Hunter: "summary",
  Analyst: "reachability",
  "Verification Lab": "reverify",
  "Patch Forge": "evidence-pack",
  "Re-Verifier": "reverify",
  "Evidence Agent": "signoff",
};

export function AuditTrailPanel({ evidence, selectedTarget, onSelect }: AuditTrailPanelProps) {
  const nodes = evidence.timeline;

  return (
    <Panel
      title="Deterministic Audit Trail"
      headerRight={
        <Link prefetch={false} href="/audit-ledger" className="flex items-center gap-1 font-data text-[9.5px] text-text-dim transition-colors hover:text-amber">
          full ledger
          <IconArrowUpRight size={10} strokeWidth={1.5} />
        </Link>
      }
      bodyClassName="flex flex-col gap-0.5 p-2"
    >
      {nodes.map((node, i) => {
        const isLast = i === nodes.length - 1;
        const target = HIGHLIGHT_TARGET[node.actor] ?? "summary";
        const isSelected = selectedTarget === target;
        return (
          <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            key={i}
            type="button"
            title={formatTimestampUtc(node.ts)}
            onClick={() => onSelect(target)}
            className={clsx(
              "flex items-start gap-1.5 px-1.5 py-1.5 text-left font-data text-[10.5px] transition-colors",
              isSelected ? "bg-amber-soft" : "hover:bg-white/[0.02]"
            )}
          >
            <span className="shrink-0 text-text-dim">{isLast ? "└─" : "├─"}</span>
            <span className="leading-snug">
              <span className={clsx("font-medium", isSelected ? "text-amber" : "text-text")}>{node.actor}</span>
              <div className="mt-0.5 text-[9.5px] text-text-dim">{node.action}</div>
            </span>
          </button>
        );
      })}
    </Panel>
  );
}
