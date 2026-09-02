"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";
import { IconPlayerPlay } from "@tabler/icons-react";
import { evaluatePolicyLive, SentinelApiError, type PolicyEvalResult } from "@/lib/sentinel/api";

const decisionStyle: Record<PolicyEvalResult["decision"], string> = {
  allowed: "border-success/40 bg-success/10 text-success",
  blocked: "border-danger/40 bg-danger/10 text-danger",
  requires_human: "border-warning/40 bg-warning/10 text-warning",
};

const decisionLabel: Record<PolicyEvalResult["decision"], string> = {
  allowed: "allowed",
  blocked: "blocked",
  requires_human: "requires human",
};

export function PolicySimulator() {
  const [input, setInput] = useState("patch-forge: deploy production");
  const [result, setResult] = useState<PolicyEvalResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const run = async () => {
    const [agentPart, ...rest] = input.split(":");
    const agent = (agentPart ?? "").trim();
    const action = rest.join(":").trim();
    if (!agent || !action) return;
    setRunning(true);
    setError(null);
    try {
      const evalResult = await evaluatePolicyLive(agent, action);
      setResult(evalResult);
    } catch (err) {
      setError(err instanceof SentinelApiError ? err.message : "Could not reach the policy engine.");
      setResult(null);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-border-soft p-2.5">
      <p className="mb-1.5 text-[9.5px] uppercase tracking-[0.06em] text-text-dim">
        simulate a call — evaluates against the real Gateway policy (identity.evaluate), nothing executes
      </p>
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="patch-forge: deploy production"
          className="w-full border border-border-soft bg-black/20 px-2 py-1.5 font-data text-[10.5px] text-text placeholder:text-text-dim focus:outline-none"
        />
        <button
          type="button"
          onClick={run}
          disabled={running}
          className="rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          <IconPlayerPlay size={11} strokeWidth={1.5} />
          {running ? "running…" : "run"}
        </button>
      </div>
      {error && <p className="mt-2 text-[10px] text-danger">{error}</p>}
      <AnimatePresence mode="wait">
        {result && (
          <motion.div
            key={`${result.agent}:${result.action}:${result.decision}`}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="mt-2 flex flex-col gap-1"
          >
            <span
              className={clsx(
                "flex w-fit items-center gap-1 border px-2 py-0.5 font-data text-[10px] uppercase tracking-[0.05em]",
                decisionStyle[result.decision]
              )}
            >
              {decisionLabel[result.decision]}
            </span>
            <p className="text-[10px] leading-snug text-text-muted">{result.reason}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
