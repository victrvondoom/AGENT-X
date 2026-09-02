"use client";

import { useState } from "react";
import { IconX } from "@tabler/icons-react";
import { TopBar } from "../command-center/TopBar";
import { IconRail } from "../command-center/IconRail";
import { AgentRegistryPanel } from "./AgentRegistryPanel";
import { AgentIdentityPanel } from "./AgentIdentityPanel";
import { AgentGatewayPanel } from "./gateway/AgentGatewayPanel";
import { ModelArmorPanel } from "./ModelArmorPanel";
import { useRegistry } from "@/lib/sentinel/hooks";
import type { AgentId } from "@/lib/types";

export function GovernancePage() {
  const [selectedAgent, setSelectedAgent] = useState<AgentId | null>(null);
  const { agents, loading, error } = useRegistry();

  const handleSelect = (id: AgentId) => setSelectedAgent((prev) => (prev === id ? null : id));
  const selectedName = agents.find((a) => a.id === selectedAgent)?.name;

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center gap-3 border-b border-border-soft px-4 py-3">
            <h1 className="text-[14px] font-medium text-text">Governance &amp; Control Plane</h1>
            <span className="text-[11px] text-text-muted">agent registry, identity, gateway, and guardrails</span>
            {selectedAgent && (
              <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                type="button"
                onClick={() => setSelectedAgent(null)}
                className="ml-auto flex items-center gap-1 border border-amber/50 bg-amber-soft px-2 py-1 font-data text-[10px] uppercase tracking-[0.05em] text-amber transition-colors hover:bg-amber/20"
              >
                filtered: {selectedName}
                <IconX size={11} strokeWidth={1.5} />
              </button>
            )}
          </div>
          <main className="grid min-h-0 flex-1 grid-cols-1 grid-rows-4 gap-3 overflow-auto p-3 lg:grid-cols-2 lg:grid-rows-2">
            <AgentRegistryPanel agents={agents} loading={loading} error={error} selectedAgent={selectedAgent} onSelect={handleSelect} />
            <AgentIdentityPanel agents={agents} loading={loading} error={error} selectedAgent={selectedAgent} />
            <AgentGatewayPanel selectedAgent={selectedAgent} />
            <ModelArmorPanel selectedAgent={selectedAgent} />
          </main>
        </div>
      </div>
    </div>
  );
}
