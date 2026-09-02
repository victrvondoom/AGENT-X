"use client";

import { ProgressPanel } from "./ProgressPanel";
import { LogPanel } from "./LogPanel";
import { useCommandCenterState } from "@/lib/sentinel/hooks";

function elapsedSince(iso: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export function SecurityAssertionWorkflow({ selectedAssetId }: { selectedAssetId: string | null }) {
  const { state, loading, error, starting, start } = useCommandCenterState(selectedAssetId);

  if (!selectedAssetId) {
    return <div className="flex h-full items-center justify-center text-[12px] text-text-dim">Select an asset to view its verification workflow.</div>;
  }
  if (loading && !state) {
    return <div className="flex h-full items-center justify-center text-[12px] text-text-dim">connecting…</div>;
  }
  if (error && !state) {
    return <div className="flex h-full items-center justify-center text-[12px] text-danger">{error}</div>;
  }
  if (!state) return null;

  const { finding, job, replaySteps, verificationLog } = state;
  const running = job?.status === "queued" || job?.status === "running";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-baseline gap-3 border-b border-border-soft px-1 pb-2">
        <h2 className="text-[13px] font-medium text-text">{finding?.id ?? selectedAssetId}</h2>
        {job ? (
          <span className="font-data text-[10px] uppercase tracking-[0.06em] text-amber">
            {running ? `${elapsedSince(job.created_at)} elapsed` : job.status}
          </span>
        ) : (
          <button
            type="button"
            onClick={start}
            disabled={starting}
            className="rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
          >
            {starting ? "starting…" : "run verification"}
          </button>
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
        <ProgressPanel steps={replaySteps} />
        <LogPanel lines={verificationLog} running={running} />
      </div>
    </div>
  );
}
