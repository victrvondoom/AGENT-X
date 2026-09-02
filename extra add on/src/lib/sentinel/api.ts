/**
 * Real REST client for the SENTINEL agent engine (backend/app/server.py).
 * No mock data, no Firestore (not installed in this deployment) - the
 * FastAPI server reads live from the job queue, evidence store, and
 * governance registry/gateway log, so a plain polling fetch here is
 * genuinely real-time, not a simulation of it.
 */

import type { AgentId, AgentRecord, GraphEdge, GraphNode, LogLine, ReplayStep } from "@/lib/types";

export const API_BASE = process.env.NEXT_PUBLIC_SENTINEL_API_URL ?? "http://localhost:8000";

/**
 * When set at build time, every GET is served from a static JSON snapshot
 * of a real engine run rather than a network call. This exists for one
 * reason: a static hosting target (Firebase Hosting, GitHub Pages) has no
 * backend behind it, and "the app is up but every panel says CONNECTION
 * LOST" is a worse demo than an honest read-only mode.
 *
 * The snapshot is not fabricated data - it is `public/api-snapshot/snapshot.json`,
 * captured with a script that hits a real running engine and writes back
 * its actual responses (25 grounded findings, one genuinely sealed evidence
 * record, real gateway and Model Armor logs). Regenerate it whenever the
 * live demo state should move forward; never hand-edit it.
 */
export const STATIC_SNAPSHOT_MODE = process.env.NEXT_PUBLIC_STATIC_SNAPSHOT === "1";

let _snapshotPromise: Promise<Record<string, unknown>> | null = null;
function loadSnapshot(): Promise<Record<string, unknown>> {
  if (!_snapshotPromise) {
    _snapshotPromise = fetch("/api-snapshot/snapshot.json")
      .then((r) => r.json())
      .catch(() => ({}));
  }
  return _snapshotPromise;
}

export class SentinelApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "SentinelApiError";
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();

  if (STATIC_SNAPSHOT_MODE) {
    if (method !== "GET") {
      // A snapshot can serve a fixed read, but it cannot start an
      // investigation or record a decision, and pretending it did would be
      // exactly the kind of fabricated result this project argues against.
      throw new SentinelApiError(
        "This is a read-only snapshot deployment showing a completed investigation. " +
          "Starting investigations, approving gates, and other actions that change state " +
          "require running the real engine - see the README's Getting Started section."
      );
    }
    const snapshot = await loadSnapshot();
    if (path in snapshot) return snapshot[path] as T;
    // Fall back to matching on the path with any query string stripped.
    // Snapshot capture and the live client can reasonably choose different
    // limits (?limit=100 vs ?limit=200) for the same logical resource, and
    // requiring an exact string match on the whole URL would make the
    // snapshot brittle to a value that carries no real distinction here.
    const base = path.split("?")[0];
    const fallbackKey = Object.keys(snapshot).find((k) => k.split("?")[0] === base);
    if (fallbackKey) return snapshot[fallbackKey] as T;
    throw new SentinelApiError(`No snapshot recorded for ${path}.`, 404);
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new SentinelApiError(
      `Could not reach the SENTINEL agent engine at ${API_BASE}. Is \`python -m app.server\` running?`
    );
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new SentinelApiError(body.detail ?? `Request to ${path} failed with ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Shapes returned by GET /api/state - the one endpoint the Command Center polls.
// ---------------------------------------------------------------------------

export interface FindingSummary {
  id: string;
  cve: string;
  severity: "critical" | "high" | "medium" | "low";
}

export interface FindingOption extends FindingSummary {
  component: string;
}

/** Full real Finding record - mirrors backend/app/schemas.py::Finding. */
export interface FullFinding {
  finding_id: string;
  severity: "critical" | "high" | "medium" | "low";
  component: string;
  version: string;
  source: string;
  advisory_id: string | null;
  advisory_url: string | null;
  cwe: string[];
  cvss_score: number | null;
  summary: string | null;
  grounding_source: string | null;
  grounding_status: string | null;
}

export function getFindings(): Promise<{ findings: FullFinding[] }> {
  return apiFetch("/api/findings");
}

export interface JobRecord {
  job_id: string;
  job_type: string;
  payload: Record<string, unknown>;
  status: "queued" | "running" | "done" | "failed";
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvidenceDocSummary {
  filename: string;
  hash: string;
  timestamp: string;
  sealed: boolean;
  /** True only when a real Nutrient DWS seal was issued (requires NUTRIENT_API_KEY). */
  dwsSealed: boolean;
  reviewStatus: "pending" | "approved" | "rejected";
}

export interface VerificationStateSummary {
  status: "EXPLOITABLE" | "VERIFIED" | "PENDING" | "RESOLVED";
  assertion: string;
  progressPct: number;
  activeAgent: AgentId;
  activeTask: string;
}

export interface CommandCenterState {
  finding: FindingSummary | null;
  findingOptions: FindingOption[];
  job: JobRecord | null;
  agents: AgentRecord[];
  graphNodes: GraphNode[];
  graphEdges: GraphEdge[];
  activeEdgeIds: string[];
  verificationLog: LogLine[];
  replaySteps: ReplayStep[];
  evidenceDoc: EvidenceDocSummary | null;
  verificationState: VerificationStateSummary;
}

export function getState(findingId?: string | null): Promise<CommandCenterState> {
  const qs = findingId ? `?finding_id=${encodeURIComponent(findingId)}` : "";
  return apiFetch<CommandCenterState>(`/api/state${qs}`);
}

export function startInvestigation(findingId?: string | null): Promise<JobRecord> {
  return apiFetch<JobRecord>("/api/investigations", {
    method: "POST",
    body: JSON.stringify({ finding_id: findingId ?? null }),
  });
}

export function abortJob(jobId: string): Promise<JobRecord> {
  return apiFetch<JobRecord>(`/api/jobs/${jobId}/abort`, { method: "POST" });
}

export interface SystemInfo {
  /** Which orchestration layer actually drives the agents: direct | adk | strands. */
  orchestrator: string;
  queue_backend: string;
  store_backend: string;
  gcp_project_id: string | null;
  demo_repo_url: string;
  nutrient_configured: boolean;
  gemini_configured: boolean;
  github_configured: boolean;
}

export function getSystemInfo(): Promise<SystemInfo> {
  return apiFetch<SystemInfo>("/api/system-info");
}

export interface HealthState {
  /** Grounding-gate outcome for the last scan. `degraded` means lookups
   *  failed outright, so the result under-reports rather than being empty. */
  scan?: {
    raw: number;
    grounded: number;
    unresolved: number;
    errored: number;
    degraded: boolean;
    from_cache: number;
    served_entirely_from_cache: boolean;
  };
  advisory_cache?: { entries: number; path: string; ttl_seconds: number };
  memory_bank: { healthy: boolean; collections: Record<string, number>; error?: string };
  evidence_integrity_pct: number;
  evidence_count: number;
  evidence_verified_count: number;
  checked_at: string;
}

export function getHealth(): Promise<HealthState> {
  return apiFetch("/api/health");
}

export interface AlertRecord {
  id: string;
  ts: string;
  severity: "critical" | "warning";
  source: "model-armor" | "gateway" | "worker";
  agent: string;
  title: string;
  detail: string;
}

export function getAlerts(limit = 100): Promise<{ alerts: AlertRecord[]; critical_count: number }> {
  return apiFetch(`/api/alerts?limit=${limit}`);
}

export interface GatewayLogEntry {
  ts: string;
  agent: string;
  action: string;
  decision: "allowed" | "blocked";
  reason: string;
}

export function getGatewayLog(limit = 200): Promise<{ log: GatewayLogEntry[] }> {
  return apiFetch(`/api/gateway-log?limit=${limit}`);
}

export interface RegistryEntry {
  id: string;
  name: string;
  version: string;
  status: "approved" | "in_review";
  owner: string;
  capabilities: string[];
}

export function getRegistry(): Promise<{ agents: RegistryEntry[] }> {
  return apiFetch("/api/registry");
}

export interface ModelArmorLogEntry {
  ts: string;
  agent: string;
  /** The untrusted input that was scanned: a README, a commit message, a file. */
  source: string;
  /**
   * "blocked" stops the pipeline; "flagged" (PII) is allowed through but
   * needs review; "clean" matched nothing. Records written before "flagged"
   * existed carry "clean" with a PII finding in `text`, which the UI
   * normalises rather than under-reporting.
   */
  severity: "clean" | "flagged" | "blocked";
  text: string;
}

export function getModelArmorLog(limit = 200): Promise<{ log: ModelArmorLogEntry[] }> {
  return apiFetch(`/api/model-armor-log?limit=${limit}`);
}

export interface PolicyEvalResult {
  agent: string;
  action: string;
  decision: "allowed" | "blocked" | "requires_human";
  reason: string;
}

export function evaluatePolicyLive(agent: string, action: string): Promise<PolicyEvalResult> {
  return apiFetch("/api/policy/evaluate", {
    method: "POST",
    body: JSON.stringify({ agent, action }),
  });
}

// ---------------------------------------------------------------------------
// Structured evidence sub-objects - mirror backend/app/schemas.py exactly
// (snake_case field names, since these are raw Pydantic .model_dump()s).
// ---------------------------------------------------------------------------

export interface RelevanceClaim {
  statement: string;
  source: string;
}

export interface RelevanceVerdict {
  finding_id: string;
  verdict: "confirmed" | "likely" | "uncertain" | "not_relevant";
  reasoning: string;
  claims: RelevanceClaim[];
}

export interface VerificationResultRecord {
  finding_id: string;
  scenario: string;
  expected: string;
  observed: string;
  result: "CONFIRMED_EXPLOITABLE" | "RESOLVED" | "INCONCLUSIVE";
  sandbox_id: string;
  duration_ms: number;
}

export interface PatchProposalRecord {
  finding_id: string;
  branch_name: string;
  files_changed: string[];
  diff: string;
  generated_test_paths: string[];
  explanation: string;
}

export interface TimelineEntryRecord {
  actor: string;
  action: string;
  ts: string;
}

export interface FullEvidenceObject {
  finding_id: string;
  repo: string;
  commit: string | null;
  timeline: TimelineEntryRecord[];
  final_status: string;
  signature: string | null;
  dws_seal: string | null;
  verdict: RelevanceVerdict | null;
  verification_results: VerificationResultRecord[];
  patch_proposal: PatchProposalRecord | null;
}

/** Shape of JobRecord.result for a completed "investigate_finding" job -
 * mirrors worker.py's run_investigation() return value exactly. */
export interface InvestigationResult {
  verdict: RelevanceVerdict;
  patch: PatchProposalRecord;
  reverify: {
    results: VerificationResultRecord[];
    final_patch_proposal: PatchProposalRecord;
  };
  evidence: FullEvidenceObject;
}

export function asInvestigationResult(result: Record<string, unknown> | null): InvestigationResult | null {
  if (!result || !("verdict" in result) || !("patch" in result) || !("reverify" in result)) return null;
  return result as unknown as InvestigationResult;
}

export function getFullEvidence(findingId: string): Promise<FullEvidenceObject> {
  return apiFetch(`/api/evidence/${encodeURIComponent(findingId)}`);
}

export function listEvidence(): Promise<{ evidence: FullEvidenceObject[] }> {
  return apiFetch("/api/evidence");
}

/**
 * Result of verifying a sealed record. The two seals attest different
 * things and can legitimately disagree: the content signature covers the
 * record's own JSON, while the DWS seal covers the CAdES-signed PDF
 * artifact. A record whose JSON is intact but whose signed PDF was swapped
 * or deleted is a materially different situation from one where both hold.
 */
export interface EvidenceVerification {
  finding_id: string;
  valid: boolean;
  content_signature: { valid: boolean; signature: string | null };
  dws: {
    present: boolean;
    valid: boolean | null;
    seal: string | null;
    recomputed?: string;
    bytes?: number;
    reason: string | null;
  };
}

export function verifyEvidence(findingId: string): Promise<EvidenceVerification> {
  return apiFetch(`/api/evidence/${encodeURIComponent(findingId)}/verify`);
}

/** URL of the real Evidence Report PDF - signed (CAdES, via Nutrient DWS)
 * or the pre-signing render. Used directly as an embed/download src.
 *
 * `download` must be requested from the server: the HTML download attribute
 * is ignored cross-origin, and the dashboard and API run on different
 * ports, so only a Content-Disposition header actually forces a save.
 */
export function evidenceDocumentUrl(
  findingId: string,
  variant: "signed" | "unsigned" = "signed",
  download = false
): string {
  const dl = download ? "&download=true" : "";
  return `${API_BASE}/api/evidence/${encodeURIComponent(findingId)}/document?variant=${variant}${dl}`;
}

// ---------------------------------------------------------------------------
// Deployment Gate
// ---------------------------------------------------------------------------

export interface DeploymentGateChecklist {
  security_resolved: boolean;
  security_status: string;
  generated_test_count: number;
  reverification_passed: boolean;
  reverification_result: string | null;
}

export interface DecisionRecord {
  finding_id: string;
  decision: "approved" | "rejected";
  actor: string;
  ts: string;
}

export interface DeploymentGateState {
  finding: { finding_id: string; cve: string; title: string; component: string; severity: string } | null;
  repo: string | null;
  commit: string | null;
  branchName: string | null;
  signature: string | null;
  sealed: boolean;
  checklist: DeploymentGateChecklist;
  decision: DecisionRecord | null;
}

export function getDeploymentGate(findingId?: string | null): Promise<DeploymentGateState> {
  const qs = findingId ? `?finding_id=${encodeURIComponent(findingId)}` : "";
  return apiFetch(`/api/deployment-gate${qs}`);
}

export interface PendingGateReview {
  finding_id: string;
  title: string;
  submitted_at: string;
  sealed: boolean;
}

export function getPendingGateReviews(): Promise<{ pending: PendingGateReview[] }> {
  return apiFetch("/api/deployment-gate/pending");
}

// ---------------------------------------------------------------------------
// Audit Ledger
// ---------------------------------------------------------------------------

export interface LedgerEntry {
  seq: number;
  findingId: string;
  title: string;
  agent: string;
  action: string;
  detail: string;
  timestamp: string;
  hash: string;
  prevHash: string;
}

export function getLedger(): Promise<{ entries: LedgerEntry[] }> {
  return apiFetch("/api/ledger");
}

/** Exact same payload format as the backend's _ledger_payload() in
 * server.py, so a client-side SHA-256 re-hash reproduces the identical
 * chain the server computed - that's what makes "verify chain" a real
 * independent recomputation instead of trusting the server's own claim. */
export function ledgerEntryPayload(entry: Pick<LedgerEntry, "findingId" | "agent" | "action" | "detail" | "timestamp">): string {
  return `${entry.findingId}|${entry.agent}|${entry.action}|${entry.detail}|${entry.timestamp}`;
}

export function postDecision(
  findingId: string,
  decision: "approved" | "rejected",
  actor = "operator"
): Promise<{ finding_id: string; decision: string; actor: string; ts: string }> {
  return apiFetch("/api/decisions", {
    method: "POST",
    body: JSON.stringify({ finding_id: findingId, decision, actor }),
  });
}
