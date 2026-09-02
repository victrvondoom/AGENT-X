"use client";

import { useMemo, useState } from "react";
import { List, type RowComponentProps } from "react-window";
import { Panel } from "../../command-center/Panel";
import { LedgerFilterBar, type LedgerFilters } from "./LedgerFilterBar";
import { VerifyChainAction } from "./VerifyChainAction";
import { GroupHeaderRow } from "./GroupHeaderRow";
import { EntryRow } from "./EntryRow";
import { LedgerEntryDetail } from "./LedgerEntryDetail";
import { useLedger, useEvidenceList } from "@/lib/sentinel/hooks";
import type { LedgerEntry } from "@/lib/sentinel/api";

type FlatRow =
  | { type: "group"; findingId: string; title: string; count: number }
  | { type: "entry"; entry: LedgerEntry };

const GROUP_ROW_HEIGHT = 34;
const ENTRY_ROW_HEIGHT = 34;

interface RowRenderProps {
  rows: FlatRow[];
  isExpanded: (id: string) => boolean;
  selectedHash: string | null;
  toggleGroup: (id: string) => void;
  selectEntry: (entry: LedgerEntry) => void;
}

function Row({ index, style, rows, isExpanded, selectedHash, toggleGroup, selectEntry }: RowComponentProps<RowRenderProps>) {
  const row = rows[index];
  if (!row) return null;
  if (row.type === "group") {
    return (
      <GroupHeaderRow
        style={style}
        findingId={row.findingId}
        title={row.title}
        count={row.count}
        expanded={isExpanded(row.findingId)}
        onToggle={() => toggleGroup(row.findingId)}
      />
    );
  }
  return <EntryRow style={style} entry={row.entry} selected={selectedHash === row.entry.hash} onSelect={() => selectEntry(row.entry)} />;
}

export function DeterministicAuditLedgerPanel() {
  const { entries: allEntries, loading, error } = useLedger();
  const { evidence } = useEvidenceList();
  const [filters, setFilters] = useState<LedgerFilters>({ agent: "all", query: "", dateFrom: "", dateTo: "" });
  // null = "no manual toggles yet, every group defaults to expanded" - a
  // pure derived default rather than something an effect needs to push in
  // once entries arrive.
  const [manualExpandedGroups, setManualExpandedGroups] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<LedgerEntry | null>(null);

  const sealedFindingIds = useMemo(() => new Set(evidence.filter((e) => e.signature).map((e) => e.finding_id)), [evidence]);
  const agents = useMemo(() => [...new Set(allEntries.map((e) => e.agent))].sort(), [allEntries]);
  const isExpanded = (id: string) => manualExpandedGroups?.has(id) ?? true;

  const filteredEntries = useMemo(() => {
    const q = filters.query.trim().toLowerCase();
    return allEntries.filter((e) => {
      if (filters.agent !== "all" && e.agent !== filters.agent) return false;
      if (q && !(e.findingId.toLowerCase().includes(q) || e.title.toLowerCase().includes(q))) return false;
      if (filters.dateFrom && e.timestamp < filters.dateFrom) return false;
      if (filters.dateTo && e.timestamp > `${filters.dateTo}T23:59:59Z`) return false;
      return true;
    });
  }, [filters, allEntries]);

  const rows: FlatRow[] = useMemo(() => {
    const groups = new Map<string, { title: string; entries: LedgerEntry[] }>();
    for (const e of filteredEntries) {
      if (!groups.has(e.findingId)) groups.set(e.findingId, { title: e.title, entries: [] });
      groups.get(e.findingId)!.entries.push(e);
    }
    const ordered = [...groups.entries()].sort(
      (a, b) => new Date(b[1].entries.at(-1)!.timestamp).getTime() - new Date(a[1].entries.at(-1)!.timestamp).getTime()
    );
    const out: FlatRow[] = [];
    for (const [findingId, group] of ordered) {
      out.push({ type: "group", findingId, title: group.title, count: group.entries.length });
      if (isExpanded(findingId)) {
        for (const entry of group.entries) out.push({ type: "entry", entry });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredEntries, manualExpandedGroups]);

  const toggleGroup = (id: string) =>
    setManualExpandedGroups((prev) => {
      // Materialize from "all expanded" into an explicit set the first
      // time anything is toggled, then flip just this one group.
      const base = prev ?? new Set(allEntries.map((e) => e.findingId));
      const next = new Set(base);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rowHeight = (index: number) => (rows[index]?.type === "group" ? GROUP_ROW_HEIGHT : ENTRY_ROW_HEIGHT);

  return (
    <Panel
      title="Deterministic Audit Ledger"
      headerRight={<VerifyChainAction entries={allEntries} />}
      className="h-full"
      bodyClassName="flex min-h-0 flex-1 flex-col"
    >
      {loading && allEntries.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-[11px] text-text-dim">connecting…</div>
      ) : error && allEntries.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-[11px] text-danger">{error}</div>
      ) : (
        <>
          <LedgerFilterBar filters={filters} onChange={setFilters} agents={agents} />
          <div className="min-h-0 flex-1">
            <List<RowRenderProps>
              rowComponent={Row}
              rowCount={rows.length}
              rowHeight={rowHeight}
              rowProps={{
                rows,
                isExpanded,
                selectedHash: selected?.hash ?? null,
                toggleGroup,
                selectEntry: setSelected,
              }}
              defaultHeight={520}
              style={{ height: "100%" }}
            />
          </div>
          {selected && (
            <LedgerEntryDetail entry={selected} sealed={sealedFindingIds.has(selected.findingId)} onClose={() => setSelected(null)} />
          )}
        </>
      )}
    </Panel>
  );
}
