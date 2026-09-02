"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { IconShieldX, IconAlertTriangle, IconCircleCheck } from "@tabler/icons-react";
import { TopBar } from "../command-center/TopBar";
import { IconRail } from "../command-center/IconRail";
import { Panel } from "../command-center/Panel";
import { useAlerts } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";
import type { AlertRecord } from "@/lib/sentinel/api";

const severityStyle: Record<AlertRecord["severity"], { border: string; text: string; Icon: typeof IconShieldX }> = {
  critical: { border: "border-l-danger", text: "text-danger", Icon: IconShieldX },
  warning: { border: "border-l-warning", text: "text-warning", Icon: IconAlertTriangle },
};

const sourceLabel: Record<AlertRecord["source"], string> = {
  "model-armor": "Model Armor",
  gateway: "Agent Gateway",
  worker: "Agent Runtime",
};

type SourceFilter = "all" | AlertRecord["source"];

export function AlertsPage() {
  const { alerts, criticalCount, loading, error } = useAlerts();
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");

  const filtered = useMemo(
    () => (sourceFilter === "all" ? alerts : alerts.filter((a) => a.source === sourceFilter)),
    [alerts, sourceFilter]
  );

  const countBySource = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const a of alerts) counts[a.source] = (counts[a.source] ?? 0) + 1;
    return counts;
  }, [alerts]);

  const filters: { key: SourceFilter; label: string }[] = [
    { key: "all", label: `all (${alerts.length})` },
    { key: "model-armor", label: `model armor (${countBySource["model-armor"] ?? 0})` },
    { key: "gateway", label: `gateway (${countBySource["gateway"] ?? 0})` },
    { key: "worker", label: `runtime (${countBySource["worker"] ?? 0})` },
  ];

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <IconRail />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-soft px-4 py-3">
            <h1 className="text-[14px] font-medium text-text">Watchdog Alerts</h1>
            <span className="text-[11px] text-text-muted">
              real blocked tool calls, guardrail interventions, and failed investigations
            </span>
            {criticalCount > 0 && (
              <span className="ml-auto flex items-center gap-1.5 border border-danger/40 bg-danger/10 px-2 py-1 font-data text-[10px] uppercase tracking-[0.06em] text-danger">
                <IconShieldX size={12} strokeWidth={1.5} />
                {criticalCount} critical
              </span>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-1 border-b border-border-soft px-3 py-2">
            {filters.map((f) => (
              <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                key={f.key}
                type="button"
                onClick={() => setSourceFilter(f.key)}
                className={clsx(
                  "border px-2.5 py-1 font-data text-[10px] uppercase tracking-[0.05em] transition-colors",
                  sourceFilter === f.key
                    ? "border-amber/50 bg-amber-soft text-amber"
                    : "border-border-soft text-text-muted hover:text-text"
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          <main className="min-h-0 flex-1 overflow-auto p-3">
            <Panel title="Alert Feed" bodyClassName="flex flex-col overflow-auto p-1">
              {loading && alerts.length === 0 ? (
                <p className="px-3 py-6 text-center text-[11px] text-text-dim">connecting to agent engine…</p>
              ) : error && alerts.length === 0 ? (
                <p className="px-3 py-6 text-center text-[11px] text-danger">{error}</p>
              ) : filtered.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-3 py-10 text-center">
                  <IconCircleCheck size={22} strokeWidth={1.4} className="text-success" />
                  <p className="text-[11.5px] text-text-muted">
                    {alerts.length === 0 ? "No alerts — every tool call so far was permitted and every scan came back clean." : "No alerts from this source."}
                  </p>
                </div>
              ) : (
                filtered.map((alert) => {
                  const tone = severityStyle[alert.severity];
                  const Icon = tone.Icon;
                  return (
                    <div
                      key={alert.id}
                      className={clsx("flex items-start gap-2.5 border-l-2 border-b border-border-soft bg-black/20 px-3 py-2.5 last:border-b-0", tone.border)}
                    >
                      <Icon size={14} strokeWidth={1.5} className={clsx("mt-0.5 shrink-0", tone.text)} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline gap-2">
                          <span className="text-[11.5px] text-text">{alert.title}</span>
                          <span className={clsx("font-data text-[9px] uppercase tracking-[0.06em]", tone.text)}>{alert.severity}</span>
                        </div>
                        <p className="mt-1 text-[10.5px] leading-snug text-text-muted">{alert.detail}</p>
                        <p className="mt-1 font-data text-[9px] text-text-dim">
                          {sourceLabel[alert.source]} · {alert.agent} · {formatTimestampUtc(alert.ts)}
                        </p>
                      </div>
                    </div>
                  );
                })
              )}
            </Panel>
          </main>
        </div>
      </div>
    </div>
  );
}
