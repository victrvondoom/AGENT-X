import Link from "next/link";
import { IconArrowUpRight, IconX } from "@tabler/icons-react";
import { formatTimestampUtc } from "@/lib/format";
import type { LedgerEntry } from "@/lib/sentinel/api";

export function LedgerEntryDetail({
  entry,
  sealed,
  onClose,
}: {
  entry: LedgerEntry;
  sealed: boolean;
  onClose: () => void;
}) {
  const isSealed = sealed;

  return (
    <div className="flex flex-col gap-1.5 border-t border-amber/30 bg-amber-soft/40 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] text-text">
          <span className="font-data uppercase text-amber">{entry.agent}</span> — {entry.action}
        </p>
        <button type="button" onClick={onClose} className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900">
          <IconX size={13} strokeWidth={1.5} />
        </button>
      </div>
      <p className="text-[10.5px] text-text-muted">{entry.detail}</p>
      <div className="grid grid-cols-1 gap-x-4 gap-y-1 font-data text-[9.5px] text-text-dim sm:grid-cols-2">
        <span>seq #{entry.seq}</span>
        <span>{formatTimestampUtc(entry.timestamp)}</span>
        <span className="truncate" title={entry.prevHash}>
          prev: {entry.prevHash.slice(0, 16)}…
        </span>
        <span className="truncate" title={entry.hash}>
          hash: {entry.hash.slice(0, 16)}…
        </span>
      </div>
      {isSealed && (
        <Link
          prefetch={false}
          href={`/evidence?finding_id=${encodeURIComponent(entry.findingId)}`}
          className="mt-1 flex w-fit items-center gap-1 font-data text-[10px] text-amber hover:underline"
        >
          view {entry.findingId} Evidence Final Report
          <IconArrowUpRight size={11} strokeWidth={1.5} />
        </Link>
      )}
    </div>
  );
}
