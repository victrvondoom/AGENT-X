"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { motion, AnimatePresence } from "framer-motion";
import { IconMinimize } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import { DiffCodeBlock } from "./DiffCodeBlock";
import { parseUnifiedDiff } from "@/lib/format";
import type { PatchProposalRecord } from "@/lib/sentinel/api";

/** The one real diff string (PatchProposal.diff) covers a single file - the
 * one named in its own "+++ b/<file>" header. Other files in files_changed
 * (e.g. a package.json version bump) have no captured line-level diff, so
 * they're shown as real prose (the explanation) rather than a fabricated
 * diff - an honestly narrower panel beats a padded one. */
function diffTargetFile(diff: string): string | null {
  const match = diff.match(/^\+\+\+ b\/(.+)$/m);
  return match ? match[1] : null;
}

export function CodePanels({ patch }: { patch: PatchProposalRecord }) {
  const diffLines = useMemo(() => parseUnifiedDiff(patch.diff), [patch.diff]);
  const diffFile = useMemo(() => diffTargetFile(patch.diff), [patch.diff]);
  const [expanded, setExpanded] = useState<string | null>(null);

  const tabs = patch.files_changed.map((file) => ({
    key: file,
    label: file,
    hasDiff: file === diffFile,
  }));

  if (expanded) {
    const active = tabs.find((t) => t.key === expanded) ?? tabs[0];
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1 border border-border-soft text-[11px] uppercase tracking-[0.06em]">
          {tabs.map((t) => (
            <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
              key={t.key}
              type="button"
              onClick={() => setExpanded(t.key)}
              className={clsx("px-3 py-1.5 transition-colors", t.key === expanded ? "bg-amber-soft text-amber" : "text-text-muted hover:text-text")}
            >
              {t.label}
            </button>
          ))}
          <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            type="button"
            onClick={() => setExpanded(null)}
            title="Collapse"
            className="ml-auto flex items-center gap-1 px-3 py-1.5 text-text-muted hover:text-text"
          >
            <IconMinimize size={13} strokeWidth={1.5} />
            collapse
          </button>
        </div>
        <motion.div key={active.key} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.15 }}>
          <Panel title={active.label}>
            {active.hasDiff ? (
              <DiffCodeBlock lines={diffLines} />
            ) : (
              <p className="p-3 text-[11px] leading-snug text-text-muted">{patch.explanation}</p>
            )}
          </Panel>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <AnimatePresence>
        {tabs.map((t) => (
          <motion.div key={t.key} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.15 }}>
            <div
              role="button"
              tabIndex={0}
              onClick={() => setExpanded(t.key)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setExpanded(t.key);
                }
              }}
              className="block w-full text-left"
            >
              <Panel title={t.label} className="h-full cursor-pointer transition-colors hover:border-text-dim">
                {t.hasDiff ? (
                  <DiffCodeBlock lines={diffLines.slice(0, 8)} />
                ) : (
                  <p className="p-3 line-clamp-4 text-[11px] leading-snug text-text-muted">{patch.explanation}</p>
                )}
              </Panel>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
