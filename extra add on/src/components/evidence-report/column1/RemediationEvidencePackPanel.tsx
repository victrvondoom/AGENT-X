"use client";

import { useMemo } from "react";
import clsx from "clsx";
import { IconCopy, IconCheck, IconX } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import { parseUnifiedDiff, truncateHash } from "@/lib/format";
import { useCopyToClipboard } from "@/lib/useCopyToClipboard";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

export function RemediationEvidencePackPanel({ evidence, highlighted }: { evidence: FullEvidenceObject; highlighted: boolean }) {
  const { state: copyState, copy } = useCopyToClipboard();
  const diff = evidence.patch_proposal?.diff ?? "";
  const lines = useMemo(() => parseUnifiedDiff(diff), [diff]);
  const commitHash = evidence.commit ?? "no commit yet";
  const file = evidence.patch_proposal?.files_changed[0] ?? "no file changed yet";

  const handleCopy = () => copy(commitHash);

  return (
    <Panel
      title="Remediation Evidence Pack"
      className={clsx("transition-shadow", highlighted && "ring-1 ring-amber/50 border-amber/40")}
      bodyClassName="flex flex-col gap-2 p-2"
    >
      {lines.length === 0 ? (
        <p className="p-2 text-[11px] text-text-dim">No patch generated yet for this finding.</p>
      ) : (
        <pre className="overflow-x-auto p-1.5 font-data text-[10px] leading-[1.6]">
          {lines.map((line, i) => (
            <div
              key={i}
              className={clsx(
                "flex gap-2 whitespace-pre px-1",
                line.kind === "removed" && "bg-danger/10 text-danger/90",
                line.kind === "added" && "bg-success/10 text-success/90",
                line.kind === "header" && "text-text-dim",
                line.kind === "context" && "text-text-muted"
              )}
            >
              <span className="w-3 shrink-0 select-none text-text-dim">
                {line.kind === "removed" ? "-" : line.kind === "added" ? "+" : ""}
              </span>
              <span>{line.text}</span>
            </div>
          ))}
        </pre>
      )}

      <div className="flex items-center justify-between border-t border-border-soft px-1 pt-2 font-data text-[10px]">
        <span className="truncate text-text-dim" title={file}>
          {file}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          title={copyState === "failed" ? "Copy blocked by the browser - select the hash manually" : commitHash}
          className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
        >
          {truncateHash(commitHash)}
          {copyState === "copied" ? (
            <IconCheck size={11} strokeWidth={1.5} className="text-success" />
          ) : copyState === "failed" ? (
            <IconX size={11} strokeWidth={1.5} className="text-danger" />
          ) : (
            <IconCopy size={11} strokeWidth={1.5} />
          )}
        </button>
      </div>
    </Panel>
  );
}
