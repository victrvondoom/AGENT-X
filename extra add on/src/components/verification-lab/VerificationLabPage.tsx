"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { TopBar } from "../command-center/TopBar";
import { IconRail } from "../command-center/IconRail";
import { AssetRegistryPanel } from "./AssetRegistryPanel";
import { SecurityAssertionWorkflow } from "./column2/SecurityAssertionWorkflow";
import { CompliancePosturePanel } from "./column3/CompliancePosturePanel";
import { PendingSignOffsPanel } from "./column3/PendingSignOffsPanel";
import { useFindings } from "@/lib/sentinel/hooks";

function VerificationLabInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryAssetId = searchParams.get("finding_id");
  const { findings } = useFindings();

  // Derived purely at render time: with no explicit ?finding_id=, default to
  // the jsonwebtoken finding once real findings have loaded.
  const defaultAssetId =
    findings.length > 0 ? (findings.find((f) => f.component === "jsonwebtoken") ?? findings[0]).finding_id : null;
  const selectedAssetId = queryAssetId ?? defaultAssetId;

  const selectAsset = (id: string) => router.push(`/verification-lab?finding_id=${encodeURIComponent(id)}`);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 lg:grid-cols-[0.3fr_0.45fr_0.25fr]">
          <div className="h-full min-h-0">
            <AssetRegistryPanel selectedId={selectedAssetId ?? ""} onSelect={selectAsset} />
          </div>

          <div className="h-full min-h-0">
            <SecurityAssertionWorkflow selectedAssetId={selectedAssetId} />
          </div>

          <div className="flex min-h-0 flex-col gap-3">
            <CompliancePosturePanel />
            <PendingSignOffsPanel />
          </div>
        </main>
      </div>
    </div>
  );
}

export function VerificationLabPage() {
  return (
    <Suspense fallback={null}>
      <VerificationLabInner />
    </Suspense>
  );
}
