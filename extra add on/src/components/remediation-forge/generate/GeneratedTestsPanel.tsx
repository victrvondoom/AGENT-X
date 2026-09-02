import { IconFileCode, IconSend } from "@tabler/icons-react";
import { Panel } from "../../command-center/Panel";
import type { PatchProposalRecord } from "@/lib/sentinel/api";

interface GeneratedTestsPanelProps {
  patch: PatchProposalRecord;
  onSendToVerification: () => void;
}

export function GeneratedTestsPanel({ patch, onSendToVerification }: GeneratedTestsPanelProps) {
  return (
    <Panel
      title="Generated Tests"
      headerRight={
        <button
          type="button"
          onClick={onSendToVerification}
          className="rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          <IconSend size={12} strokeWidth={1.5} />
          Send to verification
        </button>
      }
      bodyClassName="p-1"
    >
      {patch.generated_test_paths.length === 0 ? (
        <p className="px-3 py-3 text-[11px] text-text-dim">No test file paths recorded for this patch.</p>
      ) : (
        patch.generated_test_paths.map((path) => (
          <div key={path} className="flex items-center gap-2 px-3 py-1.5">
            <IconFileCode size={14} strokeWidth={1.5} className="shrink-0 text-success" />
            <span className="truncate font-data text-[11px] text-text" title={path}>
              {path}
            </span>
            <span className="ml-auto shrink-0 font-data text-[9.5px] text-text-dim">on {patch.branch_name}</span>
          </div>
        ))
      )}
    </Panel>
  );
}
