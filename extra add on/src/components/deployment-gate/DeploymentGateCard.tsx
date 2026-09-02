"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { IconCircleCheck, IconCircleDashed, IconCheck, IconX, IconLock } from "@tabler/icons-react";
import { GateChecklistRow } from "./GateChecklistRow";
import { HumanDecisionRow } from "./HumanDecisionRow";
import { ProvenancePipeline } from "./ProvenancePipeline";
import { CryptographicSealPanel } from "./CryptographicSealPanel";
import { useDeploymentGate } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";
import type { GateDecision } from "@/lib/gate-types";

const rowVariants = {
  hidden: { opacity: 0, y: 6 },
  show: { opacity: 1, y: 0 },
};

export function DeploymentGateCard() {
  const searchParams = useSearchParams();
  const findingId = searchParams.get("finding_id");
  const { gate, loading, error, deciding, approve, reject } = useDeploymentGate(findingId);

  const decision: GateDecision = gate?.decision?.decision ?? "pending";

  useEffect(() => {
    if (decision !== "pending") return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "a" || e.key === "A") approve();
      if (e.key === "r" || e.key === "R") reject();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [decision, approve, reject]);

  if (loading && !gate) {
    return (
      <div className="w-full max-w-[480px] border border-border bg-panel/80 p-6 text-center text-[12px] text-text-dim">
        connecting to agent engine…
      </div>
    );
  }

  if (error && !gate) {
    return (
      <div className="w-full max-w-[480px] border border-danger/40 bg-panel/80 p-6 text-center text-[12px] text-danger">
        {error}
      </div>
    );
  }

  if (!gate?.finding) {
    return (
      <div className="w-full max-w-[480px] border border-border bg-panel/80 p-6 text-center text-[12px] text-text-dim">
        No finding available. Start an investigation from the Command Center first.
      </div>
    );
  }

  const { finding, checklist, branchName, repo, signature } = gate;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className={`gate-spotlight relative w-full max-w-[480px] border border-border bg-panel/80 p-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] ${
        decision === "pending" ? "gate-pending-glow" : ""
      }`}
    >
      <ProvenancePipeline decision={decision} />

      <div className="mb-1 font-data text-[11px] text-text-muted">
        {repo ?? "no repo yet"} {branchName && `· ${branchName}`}
      </div>
      <h1 className="text-[13px] font-medium text-text">
        {finding.finding_id} — {finding.title}
      </h1>

      <motion.div
        initial="hidden"
        animate="show"
        transition={{ staggerChildren: 0.06, delayChildren: 0.1 }}
        className="mt-4 flex flex-col"
      >
        <motion.div variants={rowVariants} transition={{ duration: 0.25 }}>
          <GateChecklistRow
            icon={checklist.security_resolved ? <IconCircleCheck size={17} strokeWidth={1.5} /> : <IconCircleDashed size={17} strokeWidth={1.5} />}
            label="Security condition resolved"
            status={checklist.security_status}
            tone={checklist.security_resolved ? "success" : "warning"}
          />
        </motion.div>
        <motion.div variants={rowVariants} transition={{ duration: 0.25 }}>
          <GateChecklistRow
            icon={checklist.generated_test_count > 0 ? <IconCircleCheck size={17} strokeWidth={1.5} /> : <IconCircleDashed size={17} strokeWidth={1.5} />}
            label="Generated regression tests"
            status={`${checklist.generated_test_count} file${checklist.generated_test_count === 1 ? "" : "s"} authored`}
            tone={checklist.generated_test_count > 0 ? "success" : "warning"}
          />
        </motion.div>
        <motion.div variants={rowVariants} transition={{ duration: 0.25 }}>
          <GateChecklistRow
            icon={checklist.reverification_passed ? <IconCircleCheck size={17} strokeWidth={1.5} /> : <IconCircleDashed size={17} strokeWidth={1.5} />}
            label="Re-verification"
            status={checklist.reverification_result ?? "not yet run"}
            tone={checklist.reverification_passed ? "success" : "warning"}
          />
        </motion.div>
        <motion.div variants={rowVariants} transition={{ duration: 0.25 }}>
          <HumanDecisionRow decision={decision} />
        </motion.div>
      </motion.div>

      <div className="mt-5 border-t border-border-soft pt-5">
        <AnimatePresence mode="wait">
          {decision === "pending" ? (
            <motion.div
              key="actions"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="flex flex-col gap-2"
            >
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={approve}
                  disabled={deciding || !checklist.security_resolved}
                  title={!checklist.security_resolved ? "Security condition must be resolved before approval" : undefined}
                  className="rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
                >
                  <IconCheck size={13} strokeWidth={1.5} />
                  {deciding ? "recording…" : "Approve deployment"}
                </button>
                <button
                  type="button"
                  onClick={reject}
                  disabled={deciding}
                  className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                >
                  <IconX size={13} strokeWidth={1.5} />
                  Reject
                </button>
              </div>
              <p className="text-center font-data text-[9px] uppercase tracking-[0.08em] text-text-dim">
                press <span className="text-text-muted">A</span> to approve · <span className="text-text-muted">R</span> to reject
              </p>
            </motion.div>
          ) : (
            <motion.div
              key="confirmation"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className="flex flex-col gap-2"
            >
              {signature && gate.decision && (
                <CryptographicSealPanel hash={signature} seq={0} decidedAt={formatTimestampUtc(gate.decision.ts)} />
              )}

              <div className="flex flex-col gap-2 pt-1">
                <p className="text-[11.5px] leading-snug text-text-muted">
                  {decision === "approved"
                    ? `Approved by ${gate.decision?.actor}. Evidence pack sealed on branch ${branchName ?? "(none)"}.`
                    : `Rejected by ${gate.decision?.actor}. Sent back to Patch Forge for revision.`}
                </p>
                <Link
                  prefetch={false}
                  href={`${decision === "approved" ? "/evidence" : "/remediation"}?finding_id=${encodeURIComponent(finding.finding_id)}`}
                  className="w-fit font-data text-[10.5px] text-amber hover:underline"
                >
                  {decision === "approved"
                    ? `view ${finding.finding_id} Evidence Final Report →`
                    : `back to ${finding.finding_id} Remediation Forge →`}
                </Link>
                <p className="mt-2 flex w-fit items-center gap-1 font-data text-[9px] uppercase tracking-[0.05em] text-text-dim/70">
                  <IconLock size={9} strokeWidth={1.5} />
                  decision recorded — persisted server-side
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
