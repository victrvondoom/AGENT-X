"use client";

import clsx from "clsx";
import type { ForgeState } from "@/lib/remediation-types";
import type { FindingSummary } from "@/lib/sentinel/api";

interface RemediationHeaderProps {
  finding: FindingSummary | null;
  state: ForgeState;
  onStateChange: (state: ForgeState) => void;
  verifyEnabled: boolean;
}

export function RemediationHeader({ finding, state, onStateChange, verifyEnabled }: RemediationHeaderProps) {
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border-soft px-4 py-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-[14px] font-medium text-text">
          Remediation Strategy — <span className="text-text-muted">{finding?.id ?? "no finding selected"}</span>
        </h1>
        {finding && (
          <span className="border border-danger/40 bg-danger/10 px-1.5 py-0.5 font-data text-[10px] uppercase tracking-[0.06em] text-danger">
            {finding.cve}
          </span>
        )}
      </div>

      <div className="flex items-center border border-border-soft text-[11px] uppercase tracking-[0.06em]">
        <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          type="button"
          onClick={() => onStateChange("generate")}
          className={clsx("px-3 py-1.5 transition-colors", state === "generate" ? "bg-amber-soft text-amber" : "text-text-muted hover:text-text")}
        >
          Generate
        </button>
        <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          type="button"
          onClick={() => verifyEnabled && onStateChange("verify")}
          disabled={!verifyEnabled}
          title={verifyEnabled ? undefined : "Send to verification from Generate first"}
          className={clsx(
            "border-l border-border-soft px-3 py-1.5 transition-colors",
            !verifyEnabled && "cursor-not-allowed text-text-dim/50",
            verifyEnabled && state === "verify" && "bg-amber-soft text-amber",
            verifyEnabled && state !== "verify" && "text-text-muted hover:text-text"
          )}
        >
          Verify
        </button>
      </div>
    </div>
  );
}
