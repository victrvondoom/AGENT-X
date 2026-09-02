"use client";

import { useMemo, useState } from "react";
import clsx from "clsx";
import { IconShieldX, IconAlertTriangle, IconShieldCheck } from "@tabler/icons-react";
import { Panel } from "../command-center/Panel";
import { useModelArmorLog } from "@/lib/sentinel/hooks";
import { formatTimestampUtc } from "@/lib/format";
import type { AgentId } from "@/lib/types";
import type { ModelArmorLogEntry } from "@/lib/sentinel/api";

/**
 * The guardrail feed.
 *
 * Three severities, shown as three distinct things. That distinction is the
 * point of this panel: an injection attempt is stopped, PII is allowed
 * through but needs a human to look at it, and a clean scan is neither.
 * While PII shared the "clean" label it rendered identically to content
 * where nothing was found, so every detection was effectively invisible.
 */

type Severity = "blocked" | "flagged" | "clean";

const SEVERITY = {
  blocked: {
    label: "blocked",
    dot: "bg-danger",
    text: "text-danger",
    chip: "border-danger/40 bg-danger/10 text-danger",
    Icon: IconShieldX,
    blurb: "injection attempt — never reached the model",
  },
  flagged: {
    label: "flagged",
    dot: "bg-amber",
    text: "text-amber",
    chip: "border-amber/40 bg-amber/10 text-amber",
    Icon: IconAlertTriangle,
    blurb: "PII detected — allowed through, needs review",
  },
  clean: {
    label: "clean",
    dot: "bg-success",
    text: "text-success",
    chip: "border-success/40 bg-success/10 text-success",
    Icon: IconShieldCheck,
    blurb: "nothing matched",
  },
} as const;

function severityOf(e: ModelArmorLogEntry): Severity {
  // Records written before "flagged" existed carry severity "clean" with a
  // PII finding in the text. Reading that back as flagged keeps the history
  // honest rather than silently under-reporting older scans.
  if (e.severity === "blocked") return "blocked";
  if (e.severity === "flagged" || /\bPII\b/.test(e.text)) return "flagged";
  return "clean";
}

/**
 * The raw finding text embeds the matching regex, e.g.
 *   prompt injection pattern matched in readme: '\bexfiltrate\b|\bsend .* to https?://'
 * which is precise but unreadable in a feed. Split it so the pattern can be
 * shown as code, and the sentence stays a sentence.
 */
function splitFinding(text: string): { summary: string; pattern: string | null } {
  // [\s\S] rather than the dotAll flag: patterns can contain newlines, and
  // the `s` flag needs an es2018 target this project does not set.
  const m = text.match(/^([\s\S]*?): '([\s\S]*)'$/);
  if (!m) return { summary: text, pattern: null };
  return { summary: m[1], pattern: m[2] };
}

export function ModelArmorPanel({ selectedAgent }: { selectedAgent: AgentId | null }) {
  const { log, loading, error } = useModelArmorLog();
  const [filter, setFilter] = useState<Severity | "all">("all");

  const scoped = useMemo(() => {
    const newestFirst = [...log].reverse();
    return selectedAgent ? newestFirst.filter((e) => e.agent === selectedAgent) : newestFirst;
  }, [log, selectedAgent]);

  const counts = useMemo(() => {
    const c = { blocked: 0, flagged: 0, clean: 0 };
    for (const e of scoped) c[severityOf(e)] += 1;
    return c;
  }, [scoped]);

  const events = filter === "all" ? scoped : scoped.filter((e) => severityOf(e) === filter);

  return (
    <Panel
      title="Model Armor — Guardrail Events"
      className="h-full"
      headerRight={
        scoped.length > 0 ? (
          <span className="font-data text-[9.5px] text-text-dim">{scoped.length} scans</span>
        ) : null
      }
      bodyClassName="flex min-h-0 flex-col"
    >
      {/* Severity summary doubles as the filter. A reviewer should be able to
          see "2 blocked" without reading the feed, then click straight to them. */}
      {scoped.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border-soft px-2.5 py-2">
          <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            type="button"
            onClick={() => setFilter("all")}
            className={clsx(
              "border px-1.5 py-0.5 font-data text-[9.5px] uppercase tracking-[0.06em] transition-colors",
              filter === "all"
                ? "border-text-dim text-text"
                : "border-border-soft text-text-dim hover:text-text-muted"
            )}
          >
            all {scoped.length}
          </button>
          {(["blocked", "flagged", "clean"] as const).map((sev) => {
            const s = SEVERITY[sev];
            const n = counts[sev];
            return (
              <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                key={sev}
                type="button"
                onClick={() => setFilter(filter === sev ? "all" : sev)}
                disabled={n === 0}
                title={s.blurb}
                className={clsx(
                  "flex items-center gap-1 border px-1.5 py-0.5 font-data text-[9.5px] uppercase tracking-[0.06em] transition-colors disabled:opacity-35",
                  filter === sev ? s.chip : "border-border-soft text-text-dim hover:text-text-muted"
                )}
              >
                <s.Icon size={10} strokeWidth={1.6} />
                {s.label} {n}
              </button>
            );
          })}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {loading && log.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] text-text-dim">connecting…</p>
        ) : error && log.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] text-danger">{error}</p>
        ) : scoped.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] leading-relaxed text-text-dim">
            no guardrail scans have run yet — every Analyst and Patch Forge prompt is scanned
            before it reaches Gemini
          </p>
        ) : events.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] text-text-dim">
            no {filter} events{selectedAgent ? ` for ${selectedAgent}` : ""}
          </p>
        ) : (
          events.map((e, i) => {
            const sev = severityOf(e);
            const s = SEVERITY[sev];
            const { summary, pattern } = splitFinding(e.text);
            return (
              <div
                key={`${e.ts}-${i}`}
                className="flex items-start gap-2 border-b border-border-soft px-2.5 py-2 last:border-b-0"
              >
                <span className={clsx("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", s.dot)} />
                <div className="min-w-0 flex-1">
                  <p className="text-[10.5px] leading-snug text-text-muted">
                    <span className={clsx("font-data uppercase", s.text)}>{s.label}</span> — {summary}
                  </p>
                  {pattern && (
                    <code className="mt-1 block overflow-x-auto whitespace-pre rounded-sm bg-black/30 px-1.5 py-1 font-data text-[9px] text-text-dim">
                      {pattern}
                    </code>
                  )}
                  <p className="mt-0.5 font-data text-[9px] text-text-dim">
                    {e.agent}
                    {/* `source` names the untrusted input that was scanned - a
                        README, a commit message, a file. It was recorded all
                        along but never shown, which left the feed saying that
                        something was scanned without saying what. */}
                    {e.source ? <> · scanned <span className="text-text-muted">{e.source}</span></> : null}
                    {" · "}
                    {formatTimestampUtc(e.ts)}
                  </p>
                </div>
              </div>
            );
          })
        )}
      </div>
    </Panel>
  );
}
