import type { ReactNode } from "react";
import clsx from "clsx";

export function GateChecklistRow({
  icon,
  label,
  status,
  tone,
}: {
  icon: ReactNode;
  label: string;
  status: string;
  tone: "success" | "warning" | "danger";
}) {
  const toneColor = { success: "text-success", warning: "text-amber", danger: "text-danger" }[tone];

  return (
    <div className="flex items-center gap-3 border-b border-border-soft py-3 last:border-b-0">
      <span className={clsx("flex h-5 w-5 shrink-0 items-center justify-center", toneColor)}>{icon}</span>
      <span className="flex-1 text-[12px] text-text">{label}</span>
      <span className={clsx("font-data text-[11px] uppercase tracking-[0.05em]", toneColor)}>{status}</span>
    </div>
  );
}
