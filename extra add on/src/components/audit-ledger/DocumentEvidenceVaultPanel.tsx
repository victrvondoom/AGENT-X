"use client";

import { useState } from "react";
import { IconFileTypePdf, IconCopy, IconCheck, IconX, IconArrowLeft } from "@tabler/icons-react";
import { Panel } from "../command-center/Panel";
import { DwsViewerSlot } from "../shared/DwsViewerSlot";
import { useEvidenceList } from "@/lib/sentinel/hooks";
import { useCopyToClipboard } from "@/lib/useCopyToClipboard";
import { truncateHash } from "@/lib/format";
import type { FullEvidenceObject } from "@/lib/sentinel/api";

function VaultRow({ doc, onOpen }: { doc: FullEvidenceObject; onOpen: (doc: FullEvidenceObject) => void }) {
  const { state: copyState, copy } = useCopyToClipboard();
  const hash = doc.signature ?? "unsigned";

  const handleCopy = () => {
    void copy(hash);
  };

  // The copy control is a sibling of the row button, not nested inside it:
  // an interactive element inside another interactive element is invalid
  // HTML, and as a role="button" span it had no keyboard handler at all, so
  // keyboard users could focus the hash but never actually copy it.
  return (
    <div className="flex items-start gap-2 border-b border-border-soft px-3 py-2 transition-colors last:border-b-0 hover:bg-white/[0.02]">
      <IconFileTypePdf size={15} strokeWidth={1.5} className="mt-0.5 shrink-0 text-text-dim" />
      <div className="min-w-0 flex-1">
        <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          type="button"
          onClick={() => onOpen(doc)}
          className="block w-full truncate text-left text-[11px] text-text hover:underline"
        >
          EVIDENCE-{doc.finding_id}.json
        </button>
        <div className="mt-1 flex items-center gap-2">
          <span
            className={`border px-1 py-0.5 font-data text-[8.5px] uppercase tracking-[0.05em] ${
              doc.signature ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-warning/10 text-warning"
            }`}
          >
            {doc.signature ? "SHA-256 sealed" : "unsigned"}
          </span>
          <button
            type="button"
            onClick={handleCopy}
            aria-label={`Copy evidence signature for ${doc.finding_id}`}
            title={copyState === "failed" ? "Copy blocked by the browser - select the hash manually" : hash}
            className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          >
            {truncateHash(hash)}
            {copyState === "copied" ? (
              <IconCheck size={10} strokeWidth={1.5} className="text-success" />
            ) : copyState === "failed" ? (
              <IconX size={10} strokeWidth={1.5} className="text-danger" />
            ) : (
              <IconCopy size={10} strokeWidth={1.5} />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export function DocumentEvidenceVaultPanel() {
  const { evidence, loading, error } = useEvidenceList();
  const [selected, setSelected] = useState<FullEvidenceObject | null>(null);

  if (selected) {
    return (
      <div className="flex flex-col gap-2">
        <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          type="button"
          onClick={() => setSelected(null)}
          className="flex w-fit items-center gap-1 font-data text-[10px] text-text-muted transition-colors hover:text-text"
        >
          <IconArrowLeft size={11} strokeWidth={1.5} />
          back to vault
        </button>
        <DwsViewerSlot
          key={selected.finding_id}
          title="Document Evidence Vault"
          filename={`EVIDENCE-${selected.finding_id}.json`}
          documentId={selected.signature ?? "unsigned"}
          verificationId={selected.finding_id}
          findingId={selected.finding_id}
        />
      </div>
    );
  }

  return (
    <Panel title="Document Evidence Vault" headerRight={<span className="font-data text-[10px] text-text-dim">{evidence.length}</span>} bodyClassName="overflow-auto">
      {loading && evidence.length === 0 ? (
        <p className="px-3 py-4 text-center text-[11px] text-text-dim">connecting…</p>
      ) : error && evidence.length === 0 ? (
        <p className="px-3 py-4 text-center text-[11px] text-danger">{error}</p>
      ) : evidence.length === 0 ? (
        <p className="px-3 py-4 text-center text-[11px] text-text-dim">no evidence sealed yet</p>
      ) : (
        evidence.map((doc) => <VaultRow key={doc.finding_id} doc={doc} onOpen={setSelected} />)
      )}
    </Panel>
  );
}
