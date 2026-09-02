import clsx from "clsx";
import type { DiffLine } from "@/lib/format";

const lineStyle: Record<DiffLine["kind"], string> = {
  removed: "bg-danger/10 text-danger/90",
  added: "bg-success/10 text-success/90",
  context: "text-text-muted",
  header: "text-text-dim",
};

const marker: Record<DiffLine["kind"], string> = {
  removed: "-",
  added: "+",
  context: " ",
  header: " ",
};

export function DiffCodeBlock({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="overflow-x-auto p-2 font-data text-[10.5px] leading-[1.6]">
      {lines.map((line, i) => (
        <div key={i} className={clsx("flex gap-2 whitespace-pre px-1", lineStyle[line.kind])}>
          <span className="w-3 shrink-0 select-none text-text-dim">{marker[line.kind]}</span>
          <span>{line.text || " "}</span>
        </div>
      ))}
    </pre>
  );
}
