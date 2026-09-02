import clsx from "clsx";
import { IconCircle, IconLoader2, IconFileCode } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { PatchProposalRecord } from "@/lib/sentinel/api";

export function TestEnginePanel({ patch, running }: { patch: PatchProposalRecord | null; running: boolean }) {
  return (
    <Panel title="Test Engine" bodyClassName="p-1">
      {running ? (
        <div className="flex items-center gap-2 px-3 py-3 text-[11.5px] text-amber">
          <IconLoader2 size={14} strokeWidth={1.5} className="animate-spin" />
          Investigation running — generated tests will appear once Patch Forge completes.
        </div>
      ) : !patch || patch.generated_test_paths.length === 0 ? (
        <div className="flex items-center gap-2 px-3 py-3 text-[11.5px] text-text-dim">
          <IconCircle size={14} strokeWidth={1.5} />
          No generated tests for this run.
        </div>
      ) : (
        patch.generated_test_paths.map((path) => (
          <div key={path} className="flex items-center justify-between gap-2 px-3 py-1.5">
            <span className="flex min-w-0 items-center gap-2 text-[11.5px] text-text">
              <IconFileCode size={14} strokeWidth={1.5} className="shrink-0 text-success" />
              <span className="truncate" title={path}>
                {path}
              </span>
            </span>
            <span className={clsx("shrink-0 font-data text-[10px] uppercase tracking-[0.06em] text-success")}>authored</span>
          </div>
        ))
      )}
    </Panel>
  );
}
