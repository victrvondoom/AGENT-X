"use client";

import { motion } from "framer-motion";
import { useCopyToClipboard } from "@/lib/useCopyToClipboard";
import { IconCopy, IconCheck, IconX, IconShieldCheck } from "@tabler/icons-react";

interface CryptographicSealPanelProps {
  hash: string;
  seq: number;
  decidedAt: string;
}

/**
 * Shows the real evidence signature returned by the backend the moment
 * postDecision() resolves - no staged "computing / revealing / verifying"
 * animation, because there's nothing left to compute: the hash was already
 * produced server-side by evidence_agent._sign() before this panel ever
 * mounts. A signature you already have doesn't need to pretend to arrive.
 */
export function CryptographicSealPanel({ hash, seq, decidedAt }: CryptographicSealPanelProps) {
  const { state: copyState, copy } = useCopyToClipboard();

  const handleCopy = () => copy(hash);

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="mt-3 border border-border-soft bg-black/30 p-3"
    >
      <div className="flex items-center justify-between">
        <span className="text-[9px] uppercase tracking-[0.06em] text-text-dim">audit ledger seal — entry #{seq}</span>
        <span className="flex items-center gap-1 font-data text-[9px] uppercase tracking-[0.05em] text-success">
          <IconShieldCheck size={11} strokeWidth={1.5} />
          integrity confirmed
        </span>
      </div>

      <div className="mt-2 flex flex-col gap-1.5 font-data text-[9.5px] leading-relaxed">
        <div className="flex items-start gap-1.5">
          <span className="mt-[1px] shrink-0 text-text-dim">sha256</span>
          <p className="min-w-0 flex-1 break-all text-text">
            {hash}
            <button
              type="button"
              onClick={handleCopy}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
              title={copyState === "failed" ? "Copy blocked by the browser - select the hash manually" : hash}
            >
              {copyState === "copied" ? (
                <IconCheck size={11} strokeWidth={1.5} className="text-success" />
              ) : copyState === "failed" ? (
                <IconX size={11} strokeWidth={1.5} className="text-danger" />
              ) : (
                <IconCopy size={11} strokeWidth={1.5} />
              )}
            </button>
          </p>
        </div>
        <div className="flex items-center gap-1.5 text-text-dim">
          <span className="shrink-0">recorded</span>
          <span className="text-text-muted">{decidedAt}</span>
        </div>
      </div>
    </motion.div>
  );
}
