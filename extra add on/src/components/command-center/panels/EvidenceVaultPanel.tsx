import Link from "next/link";
import { IconFileTypePdf, IconLock, IconArrowUpRight } from "@tabler/icons-react";
import clsx from "clsx";
import { Panel } from "../Panel";
import type { EvidenceDocSummary } from "@/lib/sentinel/api";

interface EvidenceVaultPanelProps {
  evidenceDoc: EvidenceDocSummary | null;
  findingId: string | null;
  loading?: boolean;
  error?: string | null;
}

const reviewStyle: Record<EvidenceDocSummary["reviewStatus"], string> = {
  pending: "border-warning/40 bg-warning/10 text-warning",
  approved: "border-success/40 bg-success/10 text-success",
  rejected: "border-danger/40 bg-danger/10 text-danger",
};

export function EvidenceVaultPanel({ evidenceDoc, findingId, loading, error }: EvidenceVaultPanelProps) {
  return (
    <Panel
      title="Evidence Vault"
      headerRight={
        evidenceDoc && findingId ? (
          <Link
            prefetch={false}
            href={`/evidence?finding_id=${encodeURIComponent(findingId)}`}
            className="flex items-center gap-1 text-[10px] uppercase tracking-[0.06em] text-text-dim transition-colors hover:text-amber"
          >
            full report
            <IconArrowUpRight size={10} strokeWidth={1.5} />
          </Link>
        ) : (
          <span className="text-[10px] uppercase tracking-[0.06em] text-text-dim">Evidence Vault</span>
        )
      }
      bodyClassName="p-3"
    >
      {loading && !evidenceDoc && !error ? (
        <span className="text-[11px] text-text-dim">loading…</span>
      ) : error && !evidenceDoc ? (
        <span className="text-[11px] text-danger">unreachable</span>
      ) : !evidenceDoc ? (
        <div className="flex h-full flex-col items-center justify-center gap-1 py-6 text-center">
          <IconFileTypePdf size={22} strokeWidth={1.4} className="text-text-dim" />
          <p className="text-[11px] text-text-dim">No evidence sealed yet for this finding.</p>
          <p className="text-[10px] text-text-dim">Start an investigation to generate one.</p>
        </div>
      ) : (
        <>
          <div className="flex gap-3">
            <div className="flex h-16 w-12 shrink-0 items-center justify-center border border-border-soft bg-black/30">
              <IconFileTypePdf size={22} strokeWidth={1.4} className="text-text-dim" />
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <p className="truncate text-[12px] font-medium text-text" title={evidenceDoc.filename}>
                {evidenceDoc.filename}
              </p>
              <div className="flex items-center gap-1.5">
                <IconLock
                  size={12}
                  strokeWidth={1.5}
                  className={evidenceDoc.sealed ? "text-success" : "text-text-dim"}
                />
                <span
                  className={clsx(
                    "text-[10px] uppercase tracking-[0.06em]",
                    evidenceDoc.sealed ? "text-success" : "text-text-dim"
                  )}
                  title={
                    evidenceDoc.dwsSealed
                      ? "Digitally sealed via the Nutrient DWS signing API"
                      : evidenceDoc.sealed
                        ? "SHA-256 content signature. Set NUTRIENT_API_KEY to additionally seal via Nutrient DWS."
                        : undefined
                  }
                >
                  {evidenceDoc.dwsSealed ? "DWS sealed" : evidenceDoc.sealed ? "SHA-256 sealed" : "unsigned"}
                </span>
              </div>
              <p className="truncate font-data text-[10px] text-text-dim" title={evidenceDoc.hash}>
                {evidenceDoc.hash}
              </p>
              <p className="font-data text-[10px] text-text-dim">{evidenceDoc.timestamp}</p>
            </div>
          </div>

          <div
            className={clsx(
              "mt-3 inline-flex items-center gap-1.5 border px-2 py-1 text-[10px] uppercase tracking-[0.06em]",
              reviewStyle[evidenceDoc.reviewStatus]
            )}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            human review: {evidenceDoc.reviewStatus}
          </div>
        </>
      )}
    </Panel>
  );
}
