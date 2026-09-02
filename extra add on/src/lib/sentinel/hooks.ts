"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  abortJob,
  getAlerts,
  getDeploymentGate,
  getFindings,
  getFullEvidence,
  getGatewayLog,
  getHealth,
  getLedger,
  getModelArmorLog,
  getPendingGateReviews,
  getRegistry,
  getState,
  listEvidence,
  postDecision,
  startInvestigation,
  SentinelApiError,
  type AlertRecord,
  type CommandCenterState,
  type DeploymentGateState,
  type FullEvidenceObject,
  type FullFinding,
  type GatewayLogEntry,
  type HealthState,
  type LedgerEntry,
  type ModelArmorLogEntry,
  type PendingGateReview,
  type RegistryEntry,
} from "./api";

const POLL_INTERVAL_MS = 2000;

/**
 * Generic poll-on-interval hook shared by the simpler read-only resources
 * (registry, gateway log, model armor log) - same real-time-via-polling
 * approach as useCommandCenterState, just without the job start/abort
 * mutations that only the Command Center needs.
 */
function usePolledResource<T>(fetcher: () => Promise<T>, intervalMs = POLL_INTERVAL_MS) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // One self-contained loop, restarted whenever the fetcher identity changes
  // (e.g. a findingId that resolved asynchronously after mount) - which also
  // gives an immediate re-poll instead of waiting out the interval.
  //
  // A self-scheduling timeout rather than setInterval, for three reasons
  // that each showed up as real jank:
  //
  //  1. No overlap. A cold /api/findings scan can take far longer than its
  //     own interval; setInterval would fire again mid-flight and stack
  //     concurrent duplicates of the most expensive call in the system,
  //     each one making the next slower.
  //  2. Backoff. When the engine is unreachable, retrying every 2s just
  //     produces a wall of identical failures.
  //  3. Hidden tabs stop polling entirely and re-poll the moment the tab is
  //     focused, so a backgrounded dashboard costs nothing and a returning
  //     one is fresh immediately rather than up to `intervalMs` stale.
  useEffect(() => {
    let mounted = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let inFlight = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const next = await fetcher();
        if (!mounted) return;
        setData(next);
        setError(null);
        failures = 0;
      } catch (err) {
        if (!mounted) return;
        failures += 1;
        setError(err instanceof SentinelApiError ? err.message : "Failed to reach the agent engine.");
      } finally {
        inFlight = false;
        if (mounted) setLoading(false);
      }
    };

    const tick = async () => {
      await poll();
      if (!mounted || document.hidden) return;
      const delay = failures > 0 ? Math.min(intervalMs * 2 ** failures, 30_000) : intervalMs;
      timer = setTimeout(tick, delay);
    };

    const onVisibility = () => {
      if (document.hidden) {
        if (timer) clearTimeout(timer);
      } else {
        failures = 0; // a returning user should not inherit a long backoff
        void tick();
      }
    };

    const kickoff = setTimeout(tick, 0);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      mounted = false;
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimeout(kickoff);
      if (timer) clearTimeout(timer);
    };
  }, [fetcher, intervalMs]);

  return { data, loading, error };
}

export function useRegistry() {
  const { data, loading, error } = usePolledResource<{ agents: RegistryEntry[] }>(getRegistry, 5000);
  return { agents: data?.agents ?? [], loading, error };
}

export function useGatewayLog(limit = 100) {
  const fetcher = useCallback(() => getGatewayLog(limit), [limit]);
  const { data, loading, error } = usePolledResource<{ log: GatewayLogEntry[] }>(fetcher, 2000);
  return { log: data?.log ?? [], loading, error };
}

export function useModelArmorLog(limit = 100) {
  const fetcher = useCallback(() => getModelArmorLog(limit), [limit]);
  const { data, loading, error } = usePolledResource<{ log: ModelArmorLogEntry[] }>(fetcher, 5000);
  return { log: data?.log ?? [], loading, error };
}

export function useLedger() {
  const { data, loading, error } = usePolledResource<{ entries: LedgerEntry[] }>(getLedger, 5000);
  return { entries: data?.entries ?? [], loading, error };
}

export function useFindings() {
  const { data, loading, error } = usePolledResource<{ findings: FullFinding[] }>(getFindings, 10000);
  return { findings: data?.findings ?? [], loading, error };
}

export function useEvidenceList() {
  const { data, loading, error } = usePolledResource<{ evidence: FullEvidenceObject[] }>(listEvidence, 5000);
  return { evidence: data?.evidence ?? [], loading, error };
}

export function useHealth() {
  const { data, loading, error } = usePolledResource<HealthState>(getHealth, 8000);
  return { health: data, loading, error };
}

export function useAlerts() {
  const { data, loading, error } = usePolledResource<{ alerts: AlertRecord[]; critical_count: number }>(getAlerts, 5000);
  return { alerts: data?.alerts ?? [], criticalCount: data?.critical_count ?? 0, loading, error };
}

export function usePendingGateReviews() {
  const { data, loading, error } = usePolledResource<{ pending: PendingGateReview[] }>(getPendingGateReviews, 5000);
  return { pending: data?.pending ?? [], loading, error };
}

export function useFullEvidence(findingId: string | null) {
  const fetcher = useCallback(() => {
    if (!findingId) return Promise.resolve(null);
    return getFullEvidence(findingId);
  }, [findingId]);
  const { data, loading, error } = usePolledResource<FullEvidenceObject | null>(fetcher, 5000);
  return { evidence: data, loading, error };
}

interface UseDeploymentGateResult {
  gate: DeploymentGateState | null;
  loading: boolean;
  error: string | null;
  deciding: boolean;
  approve: () => Promise<void>;
  reject: () => Promise<void>;
}

/**
 * Polls GET /api/deployment-gate and writes real decisions via
 * POST /api/decisions - the same persisted decisions.json every other page
 * (Command Center's Evidence Vault badge, this page on reload, a second
 * browser tab) reads from. No localStorage, no client-computed hash chain
 * standing in for a server record.
 */
export function useDeploymentGate(findingId?: string | null): UseDeploymentGateResult {
  const fetcher = useCallback(() => getDeploymentGate(findingId), [findingId]);
  const { data, loading, error } = usePolledResource<DeploymentGateState>(fetcher, 3000);
  const [deciding, setDeciding] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const decide = useCallback(
    async (decision: "approved" | "rejected") => {
      const targetFindingId = data?.finding?.finding_id ?? findingId;
      if (!targetFindingId) return;
      setDeciding(true);
      setLocalError(null);
      try {
        await postDecision(targetFindingId, decision);
      } catch (err) {
        setLocalError(err instanceof SentinelApiError ? err.message : "Failed to record decision.");
      } finally {
        setDeciding(false);
      }
    },
    [data?.finding?.finding_id, findingId]
  );

  return {
    gate: data,
    loading,
    error: localError ?? error,
    deciding,
    approve: () => decide("approved"),
    reject: () => decide("rejected"),
  };
}

interface UseCommandCenterStateResult {
  state: CommandCenterState | null;
  loading: boolean;
  error: string | null;
  starting: boolean;
  aborting: boolean;
  /**
   * Set only by start()/abort(), and deliberately not the same state as the
   * background poll's `error`. Every panel that shows `error` only does so
   * when it has no data yet, which is almost never true once an
   * investigation has run once - so an action failure sharing that slot was
   * invisible in the one case that matters most: the user is looking at a
   * populated dashboard, clicks Start, and the click silently does nothing.
   * It was also being clobbered within ~2s by the next successful poll tick
   * clearing the shared error before anyone could read it.
   */
  actionError: string | null;
  clearActionError: () => void;
  start: () => Promise<void>;
  abort: () => Promise<void>;
}

/**
 * Polls GET /api/state on a fixed interval. This is real-time in the sense
 * that matters here: every tick reflects the agent engine's actual current
 * condition (queue + evidence store + gateway log), not a value computed
 * once at build time. Survives the "close the browser, reopen later" test
 * because the state lives in the backend, not in React state.
 */
export function useCommandCenterState(findingId?: string | null): UseCommandCenterStateResult {
  const [state, setState] = useState<CommandCenterState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const mounted = useRef(true);
  const currentJobId = useRef<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const next = await getState(findingId);
      if (!mounted.current) return;
      setState(next);
      currentJobId.current = next.job?.job_id ?? null;
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof SentinelApiError ? err.message : "Failed to reach the agent engine.");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [findingId]);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    // Same self-scheduling loop as usePolledResource: this is the 2s poll on
    // the most-viewed page in the app, so a stacked request here is the one
    // most likely to be felt. Waiting for each tick to finish before booking
    // the next guarantees at most one investigation-state request in flight,
    // and a hidden tab stops polling until it is focused again.
    const tick = async () => {
      await poll();
      if (!mounted.current || document.hidden) return;
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    const onVisibility = () => {
      if (document.hidden) {
        if (timer) clearTimeout(timer);
      } else {
        void tick();
      }
    };

    // Fire the first fetch from a timer callback (not directly in the effect
    // body) so it goes through the same "external callback triggers
    // setState" path as every subsequent tick.
    const kickoff = setTimeout(tick, 0);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      mounted.current = false;
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimeout(kickoff);
      if (timer) clearTimeout(timer);
    };
  }, [poll]);

  const start = useCallback(async () => {
    setStarting(true);
    setActionError(null);
    try {
      await startInvestigation(findingId);
      await poll();
    } catch (err) {
      setActionError(err instanceof SentinelApiError ? err.message : "Failed to start investigation.");
    } finally {
      setStarting(false);
    }
  }, [findingId, poll]);

  const abort = useCallback(async () => {
    const jobId = currentJobId.current;
    if (!jobId) return;
    setAborting(true);
    setActionError(null);
    try {
      await abortJob(jobId);
      await poll();
    } catch (err) {
      setActionError(err instanceof SentinelApiError ? err.message : "Failed to abort job.");
    } finally {
      setAborting(false);
    }
  }, [poll]);

  const clearActionError = useCallback(() => setActionError(null), []);

  return { state, loading, error, starting, aborting, actionError, clearActionError, start, abort };
}
