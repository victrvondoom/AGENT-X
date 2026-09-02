"use client";

import { useEffect, useState } from "react";
import { Panel } from "../Panel";
import type { LogLine } from "@/lib/types";
import type { VerificationStateSummary } from "@/lib/sentinel/api";

const levelColor: Record<LogLine["level"], string> = {
  info: "text-text-muted",
  warn: "text-warning",
  error: "text-danger",
  assert: "text-amber",
};

interface VerificationRuntimePanelProps {
  verificationState: VerificationStateSummary | null;
  verificationLog: LogLine[];
  loading?: boolean;
  error?: string | null;
}

export function VerificationRuntimePanel({
  verificationState,
  verificationLog,
  loading,
  error,
}: VerificationRuntimePanelProps) {
  const [cursorTs, setCursorTs] = useState<string | null>(null);

  useEffect(() => {
    const update = () => setCursorTs(new Date().toISOString().slice(11, 23));
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, []);

  if (loading && !verificationState) {
    return (
      <Panel title="Verification Lab Runtime" bodyClassName="flex items-center justify-center p-3">
        <span className="text-[11px] text-text-dim">connecting to agent engine…</span>
      </Panel>
    );
  }

  if (error && !verificationState) {
    return (
      <Panel title="Verification Lab Runtime" bodyClassName="flex items-center justify-center p-3">
        <span className="text-[11px] text-danger">{error}</span>
      </Panel>
    );
  }

  const activeAgent = verificationState?.activeAgent ?? "hunter";
  const activeTask = verificationState?.activeTask ?? "idle";
  const progressPct = verificationState?.progressPct ?? 0;

  return (
    <Panel title="Verification Lab Runtime" bodyClassName="flex flex-col p-3 gap-3">
      <div>
        <div className="flex items-baseline justify-between text-[12px]">
          <span className="font-data text-text">
            AGENT: <span className="text-amber uppercase">{activeAgent}</span>
            <span className="text-text-dim"> — status: </span>
            <span className="text-text-muted">{activeTask}</span>
          </span>
          <span className="font-data tabular-nums text-amber">{progressPct}%</span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden bg-border-soft">
          <div
            className="h-full bg-amber transition-[width] duration-700 ease-out"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-auto border border-border-soft bg-black/30 p-2 font-data text-[11px] leading-5">
        {verificationLog.map((line) => (
          <div key={line.id} className="flex gap-2 whitespace-pre-wrap">
            <span className="shrink-0 text-text-dim">{line.ts}</span>
            <span className={levelColor[line.level]}>{line.text}</span>
          </div>
        ))}
        <div className="flex gap-2">
          <span className="text-text-dim">
            {cursorTs ?? verificationLog[verificationLog.length - 1]?.ts ?? ""}
          </span>
          <span className="text-text-muted">
            _<span className="animate-[blink-caret_1s_step-end_infinite]">_</span>
          </span>
        </div>
      </div>
    </Panel>
  );
}
