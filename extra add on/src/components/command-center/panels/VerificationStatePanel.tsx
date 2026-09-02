import clsx from "clsx";
import { Panel } from "../Panel";
import type { VerificationStateSummary } from "@/lib/sentinel/api";

const stateStyle: Record<VerificationStateSummary["status"], string> = {
  EXPLOITABLE: "text-danger",
  VERIFIED: "text-success",
  RESOLVED: "text-success",
  PENDING: "text-warning",
};

interface VerificationStatePanelProps {
  verificationState: VerificationStateSummary | null;
  loading?: boolean;
  error?: string | null;
}

export function VerificationStatePanel({ verificationState, loading, error }: VerificationStatePanelProps) {
  if (loading && !verificationState) {
    return (
      <Panel title="Verification State" bodyClassName="flex items-center justify-center p-3">
        <span className="text-[11px] text-text-dim">loading…</span>
      </Panel>
    );
  }
  if (error && !verificationState) {
    return (
      <Panel title="Verification State" bodyClassName="flex items-center justify-center p-3">
        <span className="text-[11px] text-danger">unreachable</span>
      </Panel>
    );
  }

  const status = verificationState?.status ?? "PENDING";

  return (
    <Panel title="Verification State" bodyClassName="flex flex-col justify-center gap-2 p-3">
      <div className="flex items-center gap-2">
        <span
          className={clsx(
            "h-2 w-2 rounded-full",
            status === "EXPLOITABLE" ? "bg-danger" : status === "PENDING" ? "bg-warning" : "bg-success"
          )}
        />
        <span className={clsx("text-xl font-semibold tracking-tight", stateStyle[status])}>
          {status}
        </span>
      </div>
      <div className="font-data text-[11px] leading-relaxed text-text-muted">
        <span className="text-text-dim">Assert.</span>
        <span className={stateStyle[status]}> {verificationState?.assertion.replace("assert.", "") ?? ""}</span>
      </div>
    </Panel>
  );
}
