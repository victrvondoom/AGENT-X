import { CodePanels } from "./generate/CodePanels";
import { GeneratedTestsPanel } from "./generate/GeneratedTestsPanel";
import { asInvestigationResult, type JobRecord } from "@/lib/sentinel/api";

interface GenerateViewProps {
  job: JobRecord | null;
  starting: boolean;
  onSendToVerification: () => void;
}

export function GenerateView({ job, starting, onSendToVerification }: GenerateViewProps) {
  const result = job ? asInvestigationResult(job.result) : null;
  const patch = result?.patch ?? null;

  if (!job) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 border border-dashed border-border-soft p-8 text-center">
        <p className="text-[12px] text-text-dim">No patch generated yet for this finding.</p>
        <button
          type="button"
          onClick={onSendToVerification}
          disabled={starting}
          className="rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          {starting ? "starting…" : "Generate patch"}
        </button>
      </div>
    );
  }

  if (job.status === "queued" || job.status === "running") {
    return (
      <div className="flex h-full items-center justify-center border border-dashed border-border-soft p-8 text-center text-[12px] text-text-dim">
        Investigation {job.status} — Patch Forge hasn&apos;t produced a fix yet.
      </div>
    );
  }

  if (job.status === "failed") {
    return (
      <div className="flex h-full items-center justify-center border border-dashed border-danger/40 p-8 text-center text-[12px] text-danger">
        Investigation failed: {job.error}
      </div>
    );
  }

  if (!patch) {
    return (
      <div className="flex h-full items-center justify-center border border-dashed border-border-soft p-8 text-center text-[12px] text-text-dim">
        Job completed but produced no patch proposal.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <CodePanels patch={patch} />
      <GeneratedTestsPanel patch={patch} onSendToVerification={onSendToVerification} />
    </div>
  );
}
