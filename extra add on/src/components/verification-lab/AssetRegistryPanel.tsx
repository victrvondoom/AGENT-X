"use client";

import { useMemo, useState } from "react";
import { List, type RowComponentProps } from "react-window";
import clsx from "clsx";
import { IconSearch, IconCircleCheck, IconCircle } from "@tabler/icons-react";
import { Panel } from "../command-center/Panel";
import { useFindings, useEvidenceList } from "@/lib/sentinel/hooks";

const ROW_HEIGHT = 30;

interface AssetRow {
  findingId: string;
  component: string;
  version: string;
  compliant: boolean;
  investigated: boolean;
}

interface RowRenderProps {
  rows: AssetRow[];
  selectedId: string;
  onSelect: (id: string) => void;
}

function Row({ index, style, rows, selectedId, onSelect }: RowComponentProps<RowRenderProps>) {
  const row = rows[index];
  if (!row) return null;
  const selected = row.findingId === selectedId;

  return (
    <div style={style} className="px-1">
      <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
        type="button"
        onClick={() => onSelect(row.findingId)}
        className={clsx(
          "grid h-full w-full grid-cols-[1fr_54px_84px_28px] items-center gap-1 px-2 text-left transition-colors",
          selected ? "bg-amber-soft" : "hover:bg-white/[0.02]"
        )}
      >
        <span className={clsx("truncate text-[10.5px]", selected ? "text-amber" : "text-text-muted")}>{row.component}</span>
        <span className="font-data text-[10px] text-text-dim">{row.version}</span>
        <span
          className={clsx(
            "border px-1 py-0.5 text-center font-data text-[8.5px] uppercase tracking-[0.04em]",
            row.compliant ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-warning/10 text-warning"
          )}
        >
          {row.compliant ? "compliant" : "unverified"}
        </span>
        {row.investigated ? (
          <IconCircleCheck size={12} strokeWidth={1.5} className="justify-self-center text-text-muted" />
        ) : (
          <IconCircle size={12} strokeWidth={1.5} className="justify-self-center text-text-dim" />
        )}
      </button>
    </div>
  );
}

export function AssetRegistryPanel({ selectedId, onSelect }: { selectedId: string; onSelect: (id: string) => void }) {
  const { findings, loading, error } = useFindings();
  const { evidence } = useEvidenceList();
  const [query, setQuery] = useState("");

  const evidenceByFinding = useMemo(() => new Map(evidence.map((e) => [e.finding_id, e])), [evidence]);

  const rows: AssetRow[] = useMemo(() => {
    const q = query.trim().toLowerCase();
    return findings
      .filter((f) => !q || f.component.toLowerCase().includes(q) || f.version.toLowerCase().includes(q))
      .map((f) => {
        const ev = evidenceByFinding.get(f.finding_id);
        return {
          findingId: f.finding_id,
          component: f.component,
          version: f.version,
          compliant: ev?.final_status === "RESOLVED",
          investigated: Boolean(ev),
        };
      });
  }, [findings, query, evidenceByFinding]);

  return (
    <Panel title="Asset Registry (real npm audit findings)" className="h-full" bodyClassName="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1.5 border-b border-border-soft px-2 py-1.5">
        <IconSearch size={12} strokeWidth={1.5} className="shrink-0 text-text-dim" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search assets…"
          className="w-full bg-transparent font-data text-[10.5px] text-text placeholder:text-text-dim focus:outline-none"
        />
      </div>
      <div className="grid grid-cols-[1fr_54px_84px_28px] gap-1 border-b border-border-soft px-2 py-1 font-data text-[8.5px] uppercase tracking-[0.05em] text-text-dim">
        <span>Asset</span>
        <span>Version</span>
        <span>Compliance</span>
        <span className="text-center">Sealed</span>
      </div>
      <div className="min-h-0 flex-1">
        {loading && rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[11px] text-text-dim">connecting…</div>
        ) : error && rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[11px] text-danger">{error}</div>
        ) : (
          <List<RowRenderProps>
            rowComponent={Row}
            rowCount={rows.length}
            rowHeight={ROW_HEIGHT}
            rowProps={{ rows, selectedId, onSelect }}
            defaultHeight={520}
            style={{ height: "100%" }}
          />
        )}
      </div>
    </Panel>
  );
}
