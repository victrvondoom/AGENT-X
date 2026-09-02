"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";
import { Panel } from "../Panel";
import type { AgentId, AgentRecord } from "@/lib/types";

type SortKey = "name" | "version" | "health" | "memoryPct";

interface AgentRegistryPanelProps {
  agents: AgentRecord[];
  loading?: boolean;
  error?: string | null;
  selectedAgentId?: AgentId | null;
  onSelectAgent?: (id: AgentId) => void;
}

const statusDot: Record<AgentRecord["status"], string> = {
  active: "bg-success",
  idle: "bg-neutral",
  error: "bg-danger",
};

export function AgentRegistryPanel({ agents, loading, error, selectedAgentId, onSelectAgent }: AgentRegistryPanelProps) {
  const [sortKey, setSortKey] = useState<SortKey>("health");
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = useMemo(() => {
    const copy = [...agents];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortAsc ? cmp : -cmp;
    });
    return copy;
  }, [agents, sortKey, sortAsc]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  }

  const columns: { key: SortKey; label: string }[] = [
    { key: "name", label: "Agent" },
    { key: "version", label: "Version" },
    { key: "health", label: "Health" },
    { key: "memoryPct", label: "Mem" },
  ];

  return (
    <Panel title="Agent Registry & Health" bodyClassName="overflow-auto">
      {loading && agents.length === 0 ? (
        <div className="flex h-full items-center justify-center text-[11px] text-text-dim">
          connecting…
        </div>
      ) : error && agents.length === 0 ? (
        <div className="flex h-full items-center justify-center text-[11px] text-danger">{error}</div>
      ) : (
        <table className="w-full border-collapse text-[11px]">
          <thead className="sticky top-0 bg-panel">
            <tr className="border-b border-border-soft text-left text-text-dim">
              {columns.map((col) => (
                <th key={col.key} className="px-3 py-1.5 font-normal">
                  <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                    type="button"
                    onClick={() => toggleSort(col.key)}
                    className="flex items-center gap-1 uppercase tracking-[0.06em] hover:text-text-muted"
                  >
                    {col.label}
                    {sortKey === col.key &&
                      (sortAsc ? <IconChevronUp size={11} /> : <IconChevronDown size={11} />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((agent) => {
              const isSelected = selectedAgentId === agent.id;
              return (
                <tr
                  key={agent.id}
                  onClick={() => onSelectAgent?.(agent.id)}
                  className={clsx(
                    "h-7 cursor-pointer border-b border-border-soft/60 font-data transition-colors",
                    isSelected ? "bg-amber-soft" : "hover:bg-white/[0.02]"
                  )}
                >
                  <td className="px-3">
                    <span className="flex items-center gap-1.5 text-text">
                      <span className={clsx("h-1.5 w-1.5 rounded-full", statusDot[agent.status])} />
                      {agent.name}
                    </span>
                  </td>
                  <td className="px-3 text-text-muted">{agent.version}</td>
                  <td className="px-3 text-text-muted">{agent.health}%</td>
                  <td className="px-3 text-text-muted">{agent.memoryPct}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
