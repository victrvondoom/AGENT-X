"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { TopBar } from "./TopBar";
import { IconRail } from "./IconRail";
import { AgentNetworkPanel } from "./panels/AgentNetworkPanel";
import { VerificationRuntimePanel } from "./panels/VerificationRuntimePanel";
import { VerificationStatePanel } from "./panels/VerificationStatePanel";
import { ReplayTimelinePanel } from "./panels/ReplayTimelinePanel";
import { EvidenceVaultPanel } from "./panels/EvidenceVaultPanel";
import { AgentRegistryPanel } from "./panels/AgentRegistryPanel";
import { FindingSelector } from "../shared/FindingSelector";
import { useCommandCenterState } from "@/lib/sentinel/hooks";
import type { AgentId } from "@/lib/types";

const latestStepByAgent: Partial<Record<AgentId, string>> = {
  hunter: "discovery",
  analyst: "verification",
  verifier: "verification",
  "patch-forge": "patch",
  "re-verifier": "re-verify",
  watchdog: "resolution",
};

function CommandCenterInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const findingId = searchParams.get("finding_id");

  const [selectedAgentId, setSelectedAgentId] = useState<AgentId | null>(null);
  const { state, loading, error, starting, aborting, actionError, clearActionError, start, abort } =
    useCommandCenterState(findingId);

  const replaySteps = state?.replaySteps ?? [];
  const jumpedStepId = selectedAgentId
    ? latestStepByAgent[selectedAgentId] ?? replaySteps.find((s) => s.status === "active")?.id
    : null;

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar
        job={state?.job ?? null}
        starting={starting}
        aborting={aborting}
        onStart={start}
        onAbort={abort}
        actionError={actionError}
        onDismissActionError={clearActionError}
      />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-soft px-3 py-2">
            <span className="text-[10px] uppercase tracking-[0.06em] text-text-dim">investigating</span>
            <FindingSelector
              options={state?.findingOptions ?? []}
              selectedId={state?.finding?.id ?? null}
              onSelect={(id) => router.push(`/?finding_id=${encodeURIComponent(id)}`)}
            />
            {state?.job && (
              <span className="font-data text-[10px] uppercase tracking-[0.06em] text-text-dim">
                job {state.job.job_id.slice(0, 8)} · {state.job.status}
              </span>
            )}
          </div>

          <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-auto p-3 lg:grid-cols-[1.3fr_1fr_1fr] lg:grid-rows-2">
            <AgentNetworkPanel
              graphNodes={state?.graphNodes ?? []}
              graphEdges={state?.graphEdges ?? []}
              activeEdgeIds={state?.activeEdgeIds ?? []}
              finding={state?.finding ?? null}
              loading={loading}
              error={error}
              selectedAgentId={selectedAgentId}
              onSelectAgent={setSelectedAgentId}
            />

            <VerificationRuntimePanel
              verificationState={state?.verificationState ?? null}
              verificationLog={state?.verificationLog ?? []}
              loading={loading}
              error={error}
            />

            <div className="grid min-h-0 grid-rows-2 gap-3">
              <VerificationStatePanel verificationState={state?.verificationState ?? null} loading={loading} error={error} />
              <ReplayTimelinePanel replaySteps={replaySteps} jumpedStepId={jumpedStepId} loading={loading} error={error} />
            </div>

            <EvidenceVaultPanel
              evidenceDoc={state?.evidenceDoc ?? null}
              findingId={state?.finding?.id ?? null}
              loading={loading}
              error={error}
            />

            <AgentRegistryPanel
              agents={state?.agents ?? []}
              loading={loading}
              error={error}
              selectedAgentId={selectedAgentId}
              onSelectAgent={setSelectedAgentId}
            />
          </main>
        </div>
      </div>
    </div>
  );
}

export function CommandCenter() {
  return (
    <Suspense fallback={null}>
      <CommandCenterInner />
    </Suspense>
  );
}
