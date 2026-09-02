"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { TopBar } from "../command-center/TopBar";
import { IconRail } from "../command-center/IconRail";
import { RemediationHeader } from "./RemediationHeader";
import { FindingsPanel } from "./FindingsPanel";
import { GenerateView } from "./GenerateView";
import { VerifyView } from "./VerifyView";
import { useCommandCenterState } from "@/lib/sentinel/hooks";
import type { ForgeState } from "@/lib/remediation-types";

function RemediationForgeInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const findingId = searchParams.get("finding_id");

  const { state, loading, error, starting, start } = useCommandCenterState(findingId);
  const [uiState, setUiState] = useState<ForgeState>("generate");

  const job = state?.job ?? null;
  const hasRun = job !== null;

  const selectFinding = (id: string) => {
    router.push(`/remediation?finding_id=${encodeURIComponent(id)}`);
    setUiState("generate");
  };

  const handleSendToVerification = () => {
    if (!hasRun) start();
    setUiState("verify");
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <RemediationHeader
            finding={state?.finding ?? null}
            state={uiState}
            onStateChange={setUiState}
            verifyEnabled={hasRun}
          />
          <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-auto p-3 lg:grid-cols-[minmax(240px,0.32fr)_1fr]">
            <FindingsPanel
              options={state?.findingOptions ?? []}
              selectedId={state?.finding?.id ?? null}
              onSelect={selectFinding}
              loading={loading}
              error={error}
            />
            <AnimatePresence mode="wait">
              <motion.div
                key={uiState}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }}
                className="min-h-0"
              >
                {uiState === "generate" ? (
                  <GenerateView job={job} starting={starting} onSendToVerification={handleSendToVerification} />
                ) : (
                  <VerifyView job={job} />
                )}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>
    </div>
  );
}

export function RemediationForge() {
  return (
    <Suspense fallback={null}>
      <RemediationForgeInner />
    </Suspense>
  );
}
