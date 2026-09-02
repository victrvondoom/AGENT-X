"use client";

import { useState } from "react";
import clsx from "clsx";
import {
  IconFileTypePdf,
  IconShieldCheck,
  IconShieldX,
  IconLoader2,
  IconDownload,
  IconExternalLink,
} from "@tabler/icons-react";
import { Panel } from "../command-center/Panel";
import { verifyEvidence, evidenceDocumentUrl, type EvidenceVerification } from "@/lib/sentinel/api";

/**
 * The Evidence Report's seal panel.
 *
 * When Nutrient DWS is configured, the record is rendered to a PDF via
 * /build and certificate-signed via /sign - and this shows that actual
 * document, embedded and downloadable. Producing a CAdES-signed PDF and
 * then giving nobody a way to open it would defeat the point: the claim is
 * that a third party can verify this in ordinary PDF tooling, which they
 * can only do if they can get the file.
 *
 * "Verify" checks both seals independently, because they cover different
 * things and can disagree - a record whose JSON still verifies but whose
 * signed PDF was swapped is a materially different situation, and the
 * reader is told which failed rather than a single opaque pass/fail.
 */
export function DwsViewerSlot({
  title = "Evidence Seal",
  documentId,
  verificationId,
  findingId,
  dwsSealed = false,
}: {
  title?: string;
  filename?: string;
  documentId: string;
  verificationId: string;
  findingId: string;
  dwsSealed?: boolean;
}) {
  const [state, setState] = useState<"idle" | "checking" | "done" | "error">("idle");
  const [result, setResult] = useState<EvidenceVerification | null>(null);
  const pdfUrl = evidenceDocumentUrl(findingId, "signed");
  const downloadUrl = evidenceDocumentUrl(findingId, "signed", true);

  const handleVerify = async () => {
    setState("checking");
    try {
      setResult(await verifyEvidence(findingId));
      setState("done");
    } catch {
      setResult(null);
      setState("error");
    }
  };

  const overallOk = result?.valid === true;

  return (
    <Panel
      title={title}
      headerRight={
        dwsSealed ? (
          <span className="flex items-center gap-1 font-data text-[9px] uppercase tracking-[0.05em] text-success">
            <IconShieldCheck size={10} strokeWidth={1.5} />
            CAdES signed
          </span>
        ) : (
          <span className="font-data text-[9.5px] text-text-dim">{verificationId}</span>
        )
      }
      bodyClassName="flex flex-col gap-2 p-3"
    >
      {dwsSealed ? (
        <>
          <object data={pdfUrl} type="application/pdf" className="h-44 w-full border border-border-soft bg-black/20">
            {/* Shown only if the browser can't render PDFs inline. */}
            <div className="flex h-full flex-col items-center justify-center gap-1.5 text-text-dim">
              <IconFileTypePdf size={22} strokeWidth={1.2} />
              <span className="font-data text-[9.5px]">signed PDF preview unavailable in this browser</span>
            </div>
          </object>
          <div className="flex items-center gap-1.5">
            <a
              href={downloadUrl}
              className="flex items-center gap-1 border border-border-soft px-2 py-1 font-data text-[10px] uppercase tracking-[0.06em] text-text-muted transition-colors hover:border-text-dim hover:text-text"
            >
              <IconDownload size={11} strokeWidth={1.5} />
              download
            </a>
            <a
              href={pdfUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 border border-border-soft px-2 py-1 font-data text-[10px] uppercase tracking-[0.06em] text-text-muted transition-colors hover:border-text-dim hover:text-text"
            >
              <IconExternalLink size={11} strokeWidth={1.5} />
              open
            </a>
          </div>
        </>
      ) : (
        <div className="flex h-28 items-center justify-center border border-dashed border-border-soft bg-black/20 px-3">
          <div className="flex flex-col items-center gap-1.5 text-center text-text-dim">
            <IconFileTypePdf size={22} strokeWidth={1.2} />
            {/* Deliberately does not blame a missing key. Sealing also
                fails when the key is present but the DWS account is out of
                credits (HTTP 402), and telling someone to set a variable
                they already set sends them debugging the wrong thing. */}
            <span className="font-data text-[8.5px] leading-snug">
              SHA-256 content signature only — no Nutrient DWS seal on this record
            </span>
            <span className="font-data text-[8px] leading-snug text-text-dim/70">
              DWS sealing needs NUTRIENT_API_KEY set and credits available
            </span>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 flex-1 truncate font-data text-[9.5px] text-text-dim" title={documentId}>
          {documentId}
        </span>
        <button
          type="button"
          onClick={handleVerify}
          disabled={state === "checking"}
          className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
        >
          {state === "checking" && <IconLoader2 size={11} strokeWidth={1.5} className="animate-spin" />}
          {state === "done" && (overallOk ? <IconShieldCheck size={11} strokeWidth={1.5} /> : <IconShieldX size={11} strokeWidth={1.5} />)}
          {state === "error" && <IconShieldX size={11} strokeWidth={1.5} />}
          {state === "idle" && "verify seals"}
          {state === "checking" && "recomputing…"}
          {state === "done" && (overallOk ? "seals verified" : "seal mismatch")}
          {state === "error" && "engine unreachable"}
        </button>
      </div>

      {state === "done" && result && (
        <dl className="flex flex-col gap-1 border-t border-border-soft pt-2 font-data text-[9px]">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-text-dim">content signature (SHA-256 over record JSON)</dt>
            <dd className={result.content_signature.valid ? "text-success" : "text-danger"}>
              {result.content_signature.valid ? "valid" : "MISMATCH"}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-text-dim">nutrient dws seal (CAdES-signed PDF)</dt>
            <dd
              className={clsx(
                result.dws.valid === true && "text-success",
                result.dws.valid === false && "text-danger",
                result.dws.valid === null && "text-text-dim"
              )}
            >
              {result.dws.valid === true
                ? `valid · ${result.dws.bytes?.toLocaleString()} bytes`
                : result.dws.valid === false
                  ? "MISMATCH"
                  : "not sealed"}
            </dd>
          </div>
          {result.dws.reason && <p className="text-danger">{result.dws.reason}</p>}
        </dl>
      )}
    </Panel>
  );
}
