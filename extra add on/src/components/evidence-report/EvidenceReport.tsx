"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { TopBar } from "../command-center/TopBar";
import { IconRail } from "../command-center/IconRail";
import { EvidenceHeader } from "./EvidenceHeader";
import { VulnerabilitySummaryPanel } from "./column1/VulnerabilitySummaryPanel";
import { ReachabilityAnalysisPanel } from "./column1/ReachabilityAnalysisPanel";
import { SandboxVerificationPanel } from "./column1/SandboxVerificationPanel";
import { RemediationEvidencePackPanel } from "./column1/RemediationEvidencePackPanel";
import { ReVerificationResultsPanel } from "./column2/ReVerificationResultsPanel";
import { AuditTrailPanel } from "./column3/AuditTrailPanel";
import { ExecutionTimelineLink } from "./column3/ExecutionTimelineLink";
import { CompleteSecurePanel } from "./column3/CompleteSecurePanel";
import { DwsViewerSlot } from "../shared/DwsViewerSlot";
import { useEvidenceList, useFullEvidence } from "@/lib/sentinel/hooks";

function EvidenceReportInner() {
  const searchParams = useSearchParams();
  const queryFindingId = searchParams.get("finding_id");
  // Falls back to whichever evidence was sealed most recently, so this page
  // has something to show without requiring the URL param on first visit.
  // The list's error matters as much as its data. When the engine is down
  // the list comes back empty, findingId falls to null, and the detail
  // fetch then resolves to null without ever erroring - so the page would
  // report "no evidence sealed yet", which is a confident factual claim
  // about data it never actually received.
  const { evidence: evidenceList, error: listError } = useEvidenceList();
  const findingId = queryFindingId ?? evidenceList[evidenceList.length - 1]?.finding_id ?? null;

  const { evidence, loading, error } = useFullEvidence(findingId);
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <EvidenceHeader evidence={evidence} findingId={findingId} />
          {loading && !evidence ? (
            <div className="flex flex-1 items-center justify-center text-[12px] text-text-dim">connecting to agent engine…</div>
          ) : (error || listError) && !evidence ? (
            <div className="flex flex-1 items-center justify-center px-6 text-center text-[12px] text-danger">
              {error ?? listError}
            </div>
          ) : !evidence ? (
            <div className="flex flex-1 items-center justify-center text-[12px] text-text-dim">
              No evidence sealed yet. Start and complete an investigation from the Command Center first.
            </div>
          ) : (
            <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-auto p-3 lg:grid-cols-[0.85fr_1fr_1fr]">
              <div className="flex flex-col gap-3">
                <VulnerabilitySummaryPanel evidence={evidence} highlighted={selectedTarget === "summary"} />
                <ReachabilityAnalysisPanel evidence={evidence} highlighted={selectedTarget === "reachability"} />
                <SandboxVerificationPanel evidence={evidence} highlighted={selectedTarget === "reverify"} />
                <RemediationEvidencePackPanel evidence={evidence} highlighted={selectedTarget === "evidence-pack"} />
              </div>

              <div className="flex flex-col gap-3">
                <ReVerificationResultsPanel evidence={evidence} highlighted={selectedTarget === "reverify"} />
              </div>

              <div className="flex flex-col gap-3">
                <AuditTrailPanel evidence={evidence} selectedTarget={selectedTarget} onSelect={setSelectedTarget} />
                <ExecutionTimelineLink findingId={evidence.finding_id} />
                <CompleteSecurePanel evidence={evidence} highlighted={selectedTarget === "signoff"} />
                <DwsViewerSlot
                  title={evidence.dws_seal ? "Nutrient DWS Seal" : "Evidence Seal (SHA-256)"}
                  dwsSealed={Boolean(evidence.dws_seal)}
                  documentId={evidence.dws_seal ?? evidence.signature ?? "unsigned"}
                  verificationId={evidence.finding_id}
                  findingId={evidence.finding_id}
                />
              </div>
            </main>
          )}
        </div>
      </div>
    </div>
  );
}

export function EvidenceReport() {
  return (
    <Suspense fallback={null}>
      <EvidenceReportInner />
    </Suspense>
  );
}
