import { TopBar } from "../command-center/TopBar";
import { IconRail } from "../command-center/IconRail";
import { DocumentEvidenceVaultPanel } from "./DocumentEvidenceVaultPanel";
import { DwsReviewPortalPanel } from "./DwsReviewPortalPanel";
import { AuditPersistenceMonitorPanel } from "./AuditPersistenceMonitorPanel";
import { DeterministicAuditLedgerPanel } from "./ledger/DeterministicAuditLedgerPanel";

export function AuditLedgerPage() {
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 items-baseline gap-2 border-b border-border-soft px-4 py-3">
            <h1 className="text-[14px] font-medium text-text">Evidence Agent &amp; Audit Ledger</h1>
            <span className="text-[11px] text-text-muted">fleet-wide, across every finding SENTINEL has processed</span>
          </div>
          <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 lg:grid-cols-[0.38fr_1fr]">
            <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
              <DocumentEvidenceVaultPanel />
              <DwsReviewPortalPanel />
              <AuditPersistenceMonitorPanel />
            </div>
            <div className="h-full min-h-0">
              <DeterministicAuditLedgerPanel />
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
