export type AgentId =
  | "hunter"
  | "analyst"
  | "verifier"
  | "patch-forge"
  | "re-verifier"
  | "watchdog"
  | "evidence-agent";

export type AgentStatus = "active" | "idle" | "error";

export interface AgentRecord {
  id: AgentId;
  name: string;
  version: string;
  status: AgentStatus;
  health: number; // 0-100
  memoryPct: number; // 0-100
}

export type GraphNodeType = "source" | "finding" | "hub" | "agent";

export interface GraphNode {
  id: string;
  label: string;
  sublabel?: string;
  type: GraphNodeType;
  agentId?: AgentId;
  active?: boolean;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  lastMessage?: string;
}

export type LogLevel = "info" | "warn" | "error" | "assert";

export interface LogLine {
  id: string;
  ts: string;
  level: LogLevel;
  text: string;
}

export type ReplayStepStatus = "done" | "active" | "pending";

export interface ReplayStep {
  id: string;
  label: string;
  ts: string;
  status: ReplayStepStatus;
}

export type VerificationStateValue = "EXPLOITABLE" | "VERIFIED" | "PENDING" | "RESOLVED";

export interface EvidenceDoc {
  filename: string;
  hash: string;
  timestamp: string;
  sealed: boolean;
  reviewStatus: "pending" | "approved" | "rejected";
}

export interface FindingSummary {
  id: string;
  cve: string;
  severity: "critical" | "high" | "medium" | "low";
}
