import { IconSearch } from "@tabler/icons-react";

export interface LedgerFilters {
  agent: string | "all";
  query: string;
  dateFrom: string;
  dateTo: string;
}

interface LedgerFilterBarProps {
  filters: LedgerFilters;
  onChange: (filters: LedgerFilters) => void;
  agents: string[];
}

export function LedgerFilterBar({ filters, onChange, agents }: LedgerFilterBarProps) {
  const set = <K extends keyof LedgerFilters>(key: K, value: LedgerFilters[K]) =>
    onChange({ ...filters, [key]: value });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
      <div className="flex min-w-[160px] flex-1 items-center gap-1.5 border border-border-soft bg-black/20 px-2 py-1">
        <IconSearch size={12} strokeWidth={1.5} className="shrink-0 text-text-dim" />
        <input
          type="text"
          placeholder="filter by finding ID or title…"
          value={filters.query}
          onChange={(e) => set("query", e.target.value)}
          className="w-full bg-transparent font-data text-[10.5px] text-text placeholder:text-text-dim focus:outline-none"
        />
      </div>

      <select
        value={filters.agent}
        onChange={(e) => set("agent", e.target.value)}
        className="border border-border-soft bg-black/20 px-2 py-1 font-data text-[10px] text-text-muted focus:outline-none"
      >
        <option value="all">all agents</option>
        {agents.map((a) => (
          <option key={a} value={a}>
            {a}
          </option>
        ))}
      </select>

      <input
        type="date"
        value={filters.dateFrom}
        onChange={(e) => set("dateFrom", e.target.value)}
        className="border border-border-soft bg-black/20 px-2 py-1 font-data text-[10px] text-text-muted focus:outline-none"
      />
      <span className="text-text-dim">–</span>
      <input
        type="date"
        value={filters.dateTo}
        onChange={(e) => set("dateTo", e.target.value)}
        className="border border-border-soft bg-black/20 px-2 py-1 font-data text-[10px] text-text-muted focus:outline-none"
      />
    </div>
  );
}
