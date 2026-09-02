"use client";

import { STATIC_SNAPSHOT_MODE } from "@/lib/sentinel/api";

/**
 * Shown on every page of the static-hosted build. There is no backend
 * behind this deployment, so every panel is reading a fixed capture of one
 * real investigation rather than a live engine - the difference matters,
 * and hiding it would misrepresent what a visitor is looking at.
 */
export function SnapshotBanner() {
  if (!STATIC_SNAPSHOT_MODE) return null;
  return (
    <div className="flex shrink-0 items-center justify-center gap-2 border-b border-amber/30 bg-amber/10 px-3 py-1.5 text-center font-data text-[10px] uppercase tracking-[0.08em] text-amber">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber" />
      Static snapshot of one real investigation — not a live engine. Actions are disabled.{" "}
      <a
        href="https://github.com/rakeshselvaraj0108/SENTINEL#7-getting-started"
        target="_blank"
        rel="noreferrer"
        className="underline decoration-amber/50 underline-offset-2 hover:text-text"
      >
        Run it locally
      </a>
    </div>
  );
}
