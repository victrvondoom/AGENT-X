import Link from "next/link";
import { IconArrowUpRight, IconTimeline } from "@tabler/icons-react";

export function ExecutionTimelineLink({ findingId }: { findingId: string }) {
  return (
    <Link
      prefetch={false}
      href={`/?finding_id=${encodeURIComponent(findingId)}`}
      className="flex items-center gap-2 border border-border-soft bg-panel/80 px-3 py-2.5 text-[11px] text-text-muted transition-colors hover:border-text-dim hover:text-text"
    >
      <IconTimeline size={14} strokeWidth={1.5} className="shrink-0 text-text-dim" />
      <span className="flex-1">Entire execution timeline is summarized on the Command Center replay timeline</span>
      <IconArrowUpRight size={12} strokeWidth={1.5} className="shrink-0" />
    </Link>
  );
}
