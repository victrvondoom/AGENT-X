import clsx from "clsx";
import { Panel } from "../command-center/Panel";
import type { RegistryEntry } from "@/lib/sentinel/api";
import type { AgentId } from "@/lib/types";

interface AgentIdentityPanelProps {
  agents: RegistryEntry[];
  loading?: boolean;
  error?: string | null;
  selectedAgent: AgentId | null;
}

export function AgentIdentityPanel({ agents, loading, error, selectedAgent }: AgentIdentityPanelProps) {
  const rows = selectedAgent ? agents.filter((a) => a.id === selectedAgent) : agents;

  return (
    <Panel title="Agent Identity — Scoped Permissions" className="h-full" bodyClassName="flex flex-col overflow-y-auto p-1">
      {loading && agents.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-[11px] text-text-dim">connecting…</div>
      ) : error && agents.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-[11px] text-danger">{error}</div>
      ) : (
        rows.map((agent) => {
          const isLowestPrivilege = agent.capabilities.length <= 1;
          return (
            <div key={agent.id} className="flex flex-col gap-1.5 border-b border-border-soft px-2.5 py-2.5 last:border-b-0">
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-text">{agent.name}</span>
                {isLowestPrivilege && (
                  <span className="font-data text-[9px] uppercase tracking-[0.05em] text-text-dim">lowest privilege</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {agent.capabilities.map((chip) => (
                  <span key={chip} className={clsx("border border-border-soft px-1.5 py-0.5 font-data text-[9.5px] text-text-muted")}>
                    {chip}
                  </span>
                ))}
              </div>
            </div>
          );
        })
      )}
    </Panel>
  );
}
