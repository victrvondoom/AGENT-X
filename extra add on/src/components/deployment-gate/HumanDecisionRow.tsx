import { motion, AnimatePresence } from "framer-motion";
import { IconClockHour4, IconCircleCheck, IconCircleX } from "@tabler/icons-react";
import type { GateDecision } from "@/lib/gate-types";

const iconByDecision = {
  pending: { Icon: IconClockHour4, tone: "text-amber", ring: "bg-amber", label: "pending" },
  approved: { Icon: IconCircleCheck, tone: "text-success", ring: "bg-success", label: "approved" },
  rejected: { Icon: IconCircleX, tone: "text-danger", ring: "bg-danger", label: "rejected" },
} as const;

export function HumanDecisionRow({ decision }: { decision: GateDecision }) {
  const { Icon, tone, ring, label } = iconByDecision[decision];

  return (
    <div className="flex items-center gap-3 py-3">
      <span className={`relative flex h-5 w-5 shrink-0 items-center justify-center ${tone}`}>
        <AnimatePresence>
          {decision !== "pending" && (
            <motion.span
              key={decision}
              className={`absolute inset-0 rounded-full ${ring}`}
              initial={{ scale: 0.5, opacity: 0.55 }}
              animate={{ scale: 2.6, opacity: 0 }}
              transition={{ duration: 0.7, ease: "easeOut" }}
            />
          )}
        </AnimatePresence>
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={decision}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.2 }}
            className="flex"
          >
            <Icon size={17} strokeWidth={1.5} />
          </motion.span>
        </AnimatePresence>
      </span>
      <span className="flex-1 text-[12px] text-text">Human decision</span>
      <span className={`font-data text-[11px] uppercase tracking-[0.05em] ${tone}`}>{label}</span>
    </div>
  );
}
