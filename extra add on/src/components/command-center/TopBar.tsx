"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  IconShieldCheck,
  IconClipboardList,
  IconSettings2,
  IconPlayerStopFilled,
  IconTerminal2,
  IconPlayerPlayFilled,
  IconAlertTriangle,
  IconX,
} from "@tabler/icons-react";
import type { JobRecord, SystemInfo } from "@/lib/sentinel/api";
import { getSystemInfo } from "@/lib/sentinel/api";

interface TopBarProps {
  job?: JobRecord | null;
  starting?: boolean;
  aborting?: boolean;
  onStart?: () => void;
  onAbort?: () => void;
  /** Set only when a Start/Abort click itself failed - see the comment on
   *  UseCommandCenterStateResult.actionError for why this is not the same
   *  thing as the background poll's connectivity error. */
  actionError?: string | null;
  onDismissActionError?: () => void;
}

export function TopBar({
  job = null,
  starting = false,
  aborting = false,
  onStart,
  onAbort,
  actionError = null,
  onDismissActionError,
}: TopBarProps) {
  const router = useRouter();
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [showOps, setShowOps] = useState(false);
  const [showOutput, setShowOutput] = useState(false);

  const isRunning = job?.status === "queued" || job?.status === "running";

  // Auto-dismiss after a while so a stale failure does not sit in the UI
  // forever, but stays up long enough to actually be read - this is the
  // only place this message is ever shown.
  useEffect(() => {
    if (!actionError || !onDismissActionError) return;
    const t = setTimeout(onDismissActionError, 8000);
    return () => clearTimeout(t);
  }, [actionError, onDismissActionError]);

  async function handleOps() {
    if (!systemInfo) {
      try {
        setSystemInfo(await getSystemInfo());
      } catch {
        // system-info panel just stays empty if the API is unreachable
      }
    }
    setShowOps((v) => !v);
    setShowOutput(false);
  }

  return (
    <header className="relative flex h-14 shrink-0 items-center justify-between border-b border-border-soft bg-panel/60 px-4">
      <div className="flex items-center gap-3">
        <IconShieldCheck size={22} strokeWidth={1.5} className="text-amber" />
        <div className="flex items-baseline gap-2">
          <span className="text-[15px] font-semibold tracking-tight text-amber">
            SENTINEL
          </span>
          <span className="hidden text-[11px] text-text-muted sm:inline">
            Evidence-Driven Security Fleet
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5 border border-border px-2 py-1 text-[10px] uppercase tracking-[0.08em] text-text-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          v2.4 · autonomous
        </div>

        {onStart && (
          <button
            type="button"
            onClick={onStart}
            disabled={isRunning || starting}
            className="rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
          >
            <IconPlayerPlayFilled size={13} strokeWidth={1.5} />
            {starting ? "starting…" : isRunning ? "investigation running" : "start investigation"}
          </button>
        )}

        <div className="flex items-center divide-x divide-border-soft border border-border-soft">
          <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            type="button"
            onClick={() => router.push("/governance")}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] uppercase tracking-[0.06em] text-text-muted transition-colors hover:bg-white/[0.03] hover:text-text"
          >
            <IconClipboardList size={14} strokeWidth={1.5} />
            audit log
          </button>
          <button
            type="button"
            onClick={handleOps}
            className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          >
            <IconSettings2 size={14} strokeWidth={1.5} />
            ops
          </button>
          <button
            type="button"
            onClick={onAbort}
            disabled={!onAbort || !isRunning || aborting}
            className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
          >
            <IconPlayerStopFilled size={14} strokeWidth={1.5} />
            {aborting ? "aborting…" : "abort"}
          </button>
          <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            type="button"
            onClick={() => {
              setShowOutput((v) => !v);
              setShowOps(false);
            }}
            disabled={!job}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] uppercase tracking-[0.06em] text-text-muted transition-colors hover:bg-white/[0.03] hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
          >
            <IconTerminal2 size={14} strokeWidth={1.5} />
            output
          </button>
        </div>
      </div>

      {showOps && (
        <div className="absolute right-4 top-14 z-20 w-72 border border-border bg-panel p-3 font-data text-[11px] text-text-muted shadow-xl">
          <p className="mb-2 text-[10px] uppercase tracking-[0.06em] text-text-dim">system info (live)</p>
          {systemInfo ? (
            <dl className="space-y-1">
              <div className="flex justify-between">
                <dt>orchestrator</dt>
                <dd className="text-amber">{systemInfo.orchestrator}</dd>
              </div>
              <div className="flex justify-between"><dt>queue backend</dt><dd className="text-text">{systemInfo.queue_backend}</dd></div>
              <div className="flex justify-between"><dt>store backend</dt><dd className="text-text">{systemInfo.store_backend}</dd></div>
              <div className="flex justify-between"><dt>gcp project</dt><dd className="text-text">{systemInfo.gcp_project_id ?? "not set"}</dd></div>
              <div className="flex justify-between">
                <dt>gemini api</dt>
                <dd className={systemInfo.gemini_configured ? "text-success" : "text-text-dim"}>
                  {systemInfo.gemini_configured ? "configured" : "not set"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt>github api</dt>
                <dd className={systemInfo.github_configured ? "text-success" : "text-text-dim"}>
                  {systemInfo.github_configured ? "configured" : "not set"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt>nutrient dws</dt>
                <dd className={systemInfo.nutrient_configured ? "text-success" : "text-text-dim"}>
                  {systemInfo.nutrient_configured ? "configured" : "not set"}
                </dd>
              </div>
            </dl>
          ) : (
            <p>unreachable</p>
          )}
        </div>
      )}

      {actionError && (
        <div
          role="alert"
          className="absolute inset-x-4 top-14 z-30 flex items-start gap-2 border border-danger/40 bg-danger/10 px-3 py-2 text-[11px] text-danger shadow-xl"
        >
          <IconAlertTriangle size={14} strokeWidth={1.6} className="mt-0.5 shrink-0" />
          <span className="flex-1">{actionError}</span>
          {onDismissActionError && (
            <button
              type="button"
              onClick={onDismissActionError}
              aria-label="Dismiss"
              className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            >
              <IconX size={13} strokeWidth={1.8} />
            </button>
          )}
        </div>
      )}

      {showOutput && (
        <div className="absolute right-4 top-14 z-20 max-h-96 w-[28rem] overflow-auto border border-border bg-panel p-3 font-data text-[10px] text-text-muted shadow-xl">
          <p className="mb-2 text-[10px] uppercase tracking-[0.06em] text-text-dim">
            latest job result (raw, live)
          </p>
          <pre className="whitespace-pre-wrap break-words">
            {job ? JSON.stringify(job, null, 2) : "no job yet"}
          </pre>
        </div>
      )}
    </header>
  );
}
