import { describe, it, expect } from "vitest";
import { ledgerEntryPayload, type LedgerEntry } from "./api";
import { sha256Hex } from "@/lib/sha256";

/**
 * The Audit Ledger's "verify chain" button re-derives the entire hash chain
 * in the browser and compares it to what the server sent. That is the whole
 * basis of the tamper-evidence claim: the client doesn't take the server's
 * word for the hashes, it recomputes them.
 *
 * That only works while both sides build byte-identical input. If either
 * drifts, in-browser verification starts failing on data that is actually
 * intact - which looks like a tampering alert, not a bug, and is exactly
 * the kind of false alarm that destroys trust in an audit tool.
 *
 * backend/tests/test_ledger_chain.py pins the Python side to the same
 * format. These two tests are a matched pair; changing one without the
 * other should break the build.
 */

const GENESIS = "sha256:genesis";

function chainHash(prevHash: string, payload: string): string {
  return `sha256:${sha256Hex(prevHash + payload)}`;
}

function buildChain(
  events: Array<Pick<LedgerEntry, "findingId" | "agent" | "action" | "detail" | "timestamp">>
): LedgerEntry[] {
  let prev = GENESIS;
  return events.map((e, seq) => {
    const hash = chainHash(prev, ledgerEntryPayload(e));
    const entry = { ...e, title: "t", seq, hash, prevHash: prev } as LedgerEntry;
    prev = hash;
    return entry;
  });
}

/** Mirrors VerifyChainAction's recomputeChain(). */
function verifyChain(chain: LedgerEntry[]): boolean {
  let prev = chain[0]?.prevHash ?? GENESIS;
  for (const e of chain) {
    const recomputed = chainHash(prev, ledgerEntryPayload(e));
    if (e.hash !== recomputed || e.prevHash !== prev) return false;
    prev = e.hash;
  }
  return true;
}

const EVENTS = [
  { findingId: "F-1", agent: "hunter", action: "ingestion verified", detail: "detected", timestamp: "2026-01-01T00:00:00Z" },
  { findingId: "F-1", agent: "analyst", action: "pipeline event", detail: "confirmed", timestamp: "2026-01-01T00:01:00Z" },
  { findingId: "F-1", agent: "patch-forge", action: "pipeline event", detail: "patched", timestamp: "2026-01-01T00:02:00Z" },
];

describe("ledger payload contract with the Python backend", () => {
  it("builds the exact pipe-delimited string the backend builds", () => {
    // Must stay identical to app/ledger.py::ledger_payload().
    expect(
      ledgerEntryPayload({
        findingId: "F-1",
        agent: "hunter",
        action: "act",
        detail: "det",
        timestamp: "TS",
      })
    ).toBe("F-1|hunter|act|det|TS");
  });

  it("produces the same digest the backend produces for a known input", () => {
    // Cross-checked against Python:
    //   hashlib.sha256(("sha256:genesis" + "F-1|hunter|act|det|TS").encode()).hexdigest()
    const payload = ledgerEntryPayload({
      findingId: "F-1",
      agent: "hunter",
      action: "act",
      detail: "det",
      timestamp: "TS",
    });
    expect(chainHash(GENESIS, payload)).toBe(
      "sha256:7d964d4b1e6137888e1b0c7c813ea8882b3368f0c7220f79270d336f66d0d8fc"
    );
  });
});

describe("client-side chain verification", () => {
  it("accepts an intact chain", () => {
    expect(verifyChain(buildChain(EVENTS))).toBe(true);
  });

  it("rejects an altered entry", () => {
    const chain = buildChain(EVENTS);
    chain[1].detail = "quietly rewritten";
    expect(verifyChain(chain)).toBe(false);
  });

  it("rejects a deleted entry", () => {
    const chain = buildChain(EVENTS);
    chain.splice(1, 1);
    expect(verifyChain(chain)).toBe(false);
  });

  it("rejects reordered entries", () => {
    const chain = buildChain(EVENTS);
    [chain[1], chain[2]] = [chain[2], chain[1]];
    expect(verifyChain(chain)).toBe(false);
  });

  it("rejects a forged hash that does not follow from its predecessor", () => {
    const chain = buildChain(EVENTS);
    chain[2].hash = `sha256:${"0".repeat(64)}`;
    expect(verifyChain(chain)).toBe(false);
  });
});
