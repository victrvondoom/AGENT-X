"use client";

import { Panel } from "../../command-center/Panel";
import { SemicircularGauge } from "./SemicircularGauge";
import { useFindings, useEvidenceList } from "@/lib/sentinel/hooks";

export function CompliancePosturePanel() {
  const { findings } = useFindings();
  const { evidence } = useEvidenceList();

  const compliant = evidence.filter((e) => e.final_status === "RESOLVED").length;
  const total = findings.length;
  const pct = total > 0 ? (compliant / total) * 100 : 0;

  return (
    <Panel title="Current Compliance Posture" bodyClassName="flex flex-col items-center justify-center p-3">
      <SemicircularGauge pct={pct} />
      <p className="mt-1 font-data text-[9.5px] text-text-dim">
        {compliant}/{total} findings resolved with sealed evidence
      </p>
    </Panel>
  );
}
