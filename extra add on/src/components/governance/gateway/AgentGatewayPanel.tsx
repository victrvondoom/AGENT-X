"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";
import { IconPlayerPause, IconPlayerPlay } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import { PolicySimulator } from "./PolicySimulator";
import { useGatewayLog } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";
import type { AgentId } from "@/lib/types";

const decisionColor: Record<string, string> = {
  allowed: "text-success",
  blocked: "text-danger",
  "requires-human": "text-warning",
};

export function AgentGatewayPanel({ selectedAgent }: { selectedAgent: AgentId | null }) {
  const { log, loading, error } = useGatewayLog();
  const [paused, setPaused] = useState(false);
  const [snapshot, setSnapshot] = useState<typeof log | null>(null);

  const newestFirst = [...log].reverse();
  const togglePause = () => {
    if (paused) {
      setSnapshot(null);
      setPaused(false);
    } else {
      setSnapshot(newestFirst);
      setPaused(true);
    }
  };

  const visible = snapshot ?? newestFirst;
  const newSincePause = paused ? newestFirst.length - visible.length : 0;
  const displayed = selectedAgent ? visible.filter((e) => e.agent === selectedAgent) : visible;

  return (
    <Panel
      title="Agent Gateway — Recent Tool Calls"
      className="h-full"
      headerRight={
        <button
          type="button"
          onClick={togglePause}
          className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
        >
          {paused ? <IconPlayerPlay size={12} strokeWidth={1.5} /> : <IconPlayerPause size={12} strokeWidth={1.5} />}
          {paused ? `paused${newSincePause > 0 ? ` · ${newSincePause} new` : ""}` : "live"}
        </button>
      }
      bodyClassName="flex min-h-0 flex-1 flex-col"
    >
      <div className="min-h-0 flex-1 cursor-pointer overflow-y-auto" onClick={togglePause} title="click to pause / resume">
        {loading && log.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] text-text-dim">connecting…</p>
        ) : error && log.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] text-danger">{error}</p>
        ) : displayed.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] text-text-dim">
            no tool calls logged yet — every real agent action passes through this gateway
          </p>
        ) : (
          <AnimatePresence initial={false}>
            {displayed.map((e, i) => (
              <motion.div
                key={`${e.ts}-${e.agent}-${e.action}-${i}`}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.2 }}
                className="flex items-center gap-2 border-b border-border-soft px-2.5 py-1.5"
              >
                <span className="w-[76px] shrink-0 font-data text-[9px] text-text-dim">
                  {formatTimestampUtc(e.ts).slice(5, 16)}
                </span>
                <span className="w-[82px] shrink-0 truncate font-data text-[10px] uppercase text-text-muted">{e.agent}</span>
                <span className="flex-1 truncate text-[10.5px] text-text">{e.action}</span>
                <span className={clsx("shrink-0 font-data text-[9.5px] uppercase tracking-[0.05em]", decisionColor[e.decision])}>
                  {e.decision}
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>
      <PolicySimulator />
    </Panel>
  );
}
