import type { ReactNode } from "react";
import clsx from "clsx";

interface PanelProps {
  title: string;
  className?: string;
  headerRight?: ReactNode;
  children: ReactNode;
  bodyClassName?: string;
}

export function Panel({ title, className, headerRight, children, bodyClassName }: PanelProps) {
  return (
    <section
      className={clsx(
        "flex flex-col rounded-xl border border-zinc-300 bg-white/90 dark:border-zinc-800 dark:bg-[#1a1a1f]/90 backdrop-blur-md shadow-sm transition-transform hover:-translate-y-0.5",
        className
      )}
    >
      <header className="flex items-center justify-between border-b border-zinc-200 dark:border-zinc-800/80 px-4 py-3 shrink-0">
        <h2 className="text-[13px] font-semibold tracking-wide text-zinc-700 dark:text-zinc-300">
          {title}
        </h2>
        {headerRight}
      </header>
      <div className={clsx("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}
