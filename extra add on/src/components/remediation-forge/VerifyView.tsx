import { TestEnginePanel } from "./verify/TestEnginePanel";
import { ReVerificationStatusPanel } from "./verify/ReVerificationStatusPanel";
import { ClaimVsEvidencePanel } from "./verify/ClaimVsEvidencePanel";
import { asInvestigationResult, type JobRecord } from "@/lib/sentinel/api";

export function VerifyView({ job }: { job: JobRecord | null }) {
  const result = job ? asInvestigationResult(job.result) : null;
  const running = job?.status === "queued" || job?.status === "running";

  if (!job) {
    return (
      <div className="flex h-full items-center justify-center border border-dashed border-border-soft p-8 text-center text-[12px] text-text-dim">
        No investigation started yet — generate a patch first.
      </div>
    );
  }

  return (
    <div className="grid grid-rows-3 gap-3">
      <TestEnginePanel patch={result?.patch ?? null} running={running} />
      <ReVerificationStatusPanel results={result?.reverify.results ?? []} running={running} failed={job.status === "failed"} />
      <ClaimVsEvidencePanel verdict={result?.verdict ?? null} evidence={result?.evidence ?? null} />
    </div>
  );
}
