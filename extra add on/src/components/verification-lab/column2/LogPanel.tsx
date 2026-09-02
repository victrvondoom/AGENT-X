"use client";

import { useEffect, useRef } from "react";
import clsx from "clsx";
import { Panel } from "../../command-center/Panel";
import type { LogLine } from "@/lib/types";

const levelColor: Record<LogLine["level"], string> = {
  info: "text-text-muted",
  warn: "text-warning",
  error: "text-danger",
  assert: "text-amber",
};

export function LogPanel({ lines, running }: { lines: LogLine[]; running: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  });

  return (
    <Panel title="Log" headerRight={<span className="font-data text-[9.5px] text-text-dim">{running ? "live" : "final"}</span>} bodyClassName="p-0">
      <div ref={scrollRef} className="h-[220px] overflow-y-auto p-2.5 font-data text-[10px] leading-relaxed">
        {lines.length === 0 ? (
          <span className="text-text-dim">No investigation has been run for this asset yet.</span>
        ) : (
          lines.map((l) => (
            <div key={l.id} className={clsx("whitespace-pre-wrap", levelColor[l.level])}>
              {l.text}
            </div>
          ))
        )}
        {running && <span className="inline-block h-3 w-1.5 animate-pulse bg-amber/70 align-middle" />}
      </div>
    </Panel>
  );
}
