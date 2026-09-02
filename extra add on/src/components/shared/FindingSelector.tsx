"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { IconChevronDown, IconSearch } from "@tabler/icons-react";
import type { FindingOption } from "@/lib/sentinel/api";

const severityText: Record<FindingOption["severity"], string> = {
  critical: "text-danger",
  high: "text-warning",
  medium: "text-neutral",
  low: "text-success",
};

interface FindingSelectorProps {
  options: FindingOption[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/**
 * Real finding picker over the live GET /api/state findingOptions list.
 * Every page that operates on "a finding" drives it through the same
 * ?finding_id= URL param, so a selection here is shareable, survives a
 * reload, and stays consistent across pages.
 */
export function FindingSelector({ options, selectedId, onSelect }: FindingSelectorProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const selected = options.find((o) => o.id === selectedId) ?? null;
  const q = query.trim().toLowerCase();
  const filtered = q ? options.filter((o) => o.component.toLowerCase().includes(q) || o.cve.toLowerCase().includes(q)) : options;

  return (
    <div ref={containerRef} className="relative">
      <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={options.length === 0}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 border border-border-soft bg-black/20 px-2.5 py-1.5 font-data text-[10.5px] text-text transition-colors hover:border-text-dim disabled:cursor-not-allowed disabled:opacity-50"
      >
        {selected ? (
          <>
            <span className={clsx("uppercase tracking-[0.05em]", severityText[selected.severity])}>{selected.severity}</span>
            <span className="text-text-muted">{selected.component}</span>
            <span className="text-text-dim">{selected.cve}</span>
          </>
        ) : (
          <span className="text-text-dim">{options.length === 0 ? "no findings loaded" : "select a finding"}</span>
        )}
        <IconChevronDown size={12} strokeWidth={1.5} className={clsx("text-text-dim transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-[420px] w-[380px] overflow-hidden border border-border bg-panel shadow-xl">
          <div className="flex items-center gap-1.5 border-b border-border-soft px-2 py-1.5">
            <IconSearch size={12} strokeWidth={1.5} className="shrink-0 text-text-dim" />
            <input
              autoFocus
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="filter by component or advisory…"
              className="w-full bg-transparent font-data text-[10.5px] text-text placeholder:text-text-dim focus:outline-none"
            />
          </div>
          <div role="listbox" className="max-h-[370px] overflow-y-auto">
            {filtered.length === 0 ? (
              <p className="px-3 py-4 text-center text-[11px] text-text-dim">no matching findings</p>
            ) : (
              filtered.map((o) => (
                <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                  key={o.id}
                  type="button"
                  role="option"
                  aria-selected={o.id === selectedId}
                  onClick={() => {
                    onSelect(o.id);
                    setOpen(false);
                    setQuery("");
                  }}
                  className={clsx(
                    "flex w-full items-center gap-2 border-b border-border-soft px-3 py-1.5 text-left transition-colors last:border-b-0",
                    o.id === selectedId ? "bg-amber-soft" : "hover:bg-white/[0.02]"
                  )}
                >
                  <span className={clsx("w-[52px] shrink-0 font-data text-[9px] uppercase tracking-[0.05em]", severityText[o.severity])}>
                    {o.severity}
                  </span>
                  <span className={clsx("flex-1 truncate text-[11px]", o.id === selectedId ? "text-amber" : "text-text")}>{o.component}</span>
                  <span className="shrink-0 font-data text-[9.5px] text-text-dim">{o.cve}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
