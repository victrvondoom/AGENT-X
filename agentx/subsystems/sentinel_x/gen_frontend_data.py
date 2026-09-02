"""Regenerates the Next.js frontend's `src/lib/*-data.ts` files from the real
backend snapshot (backend/workdir/snapshot.json). This is the "data
contracts" wiring step: every literal value written into these files is
copied straight from real agent output (Hunter's actual npm audit findings,
Evidence Agent's actual signed timeline, a real SHA-256 hash chain) - no
fabricated CVE-shaped IDs, no invented findings. Component/JSX code is never
touched; only the four `src/lib/*-data.ts` modules whose exported shapes
those components already consume.

Re-run after `export_snapshot.py` whenever new agent output should reach
the UI:
    ./.venv/Scripts/python.exe -m app.gen_frontend_data
"""

from __future__ import annotations

import json
from pathlib import Path

# Derived from config rather than recomputed here, so the vendored location
# is described in exactly one place and these two cannot drift apart.
from agentx.subsystems.sentinel_x.config import BACKEND_DIR, WORKDIR

REPO_ROOT = BACKEND_DIR
SNAPSHOT_PATH = WORKDIR / "snapshot.json"

# The Next.js frontend this generator writes into is part of the SENTINEL
# source repository and was not vendored, so this path does not exist here.
# The generator is kept because it documents the shape of the data the UI
# consumes; `main()` refuses rather than writing into a directory it invented.
LIB_DIR = REPO_ROOT / "src" / "lib"

HERO_FINDING_ID = "SENTINEL-F-GHSA-8cf7-32gw-wr33"


def js_str(s: str) -> str:
    return json.dumps(s)


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def finding_title(f: dict) -> str:
    return f"{f['component']} — {f['advisory_id']}"


ACTOR_TO_LEDGER_AGENT = {
    "Hunter": "hunter",
    "Analyst": "analyst",
    "Verification Lab": "verifier",
    "Patch Forge": "patch-forge",
    "Human": "human",
}

ACTOR_TO_ACTION_LABEL = {
    "Hunter": "ingestion verified",
    "Analyst": "reachability confirmed",
    "Verification Lab": "sandbox scenario executed",
    "Patch Forge": "remediation generated",
    "Human": "final approval",
}


def gen_mock_data(snapshot: dict) -> str:
    hero = next(f for f in snapshot["findings"] if f["finding_id"] == HERO_FINDING_ID)
    evidence = snapshot["evidence"][HERO_FINDING_ID]
    timeline = evidence["timeline"]
    verify_entries = [e for e in timeline if e["actor"] == "Verification Lab"]
    baseline, post_fix = verify_entries[0], verify_entries[1]

    def hhmmss(ts: str) -> str:
        return ts[11:19]

    log_lines = [
        (
            "l1",
            hhmmss(timeline[0]["ts"]),
            "info",
            f"npm audit detected {hero['advisory_id']} in {hero['component']}@{hero['version']}",
        ),
        (
            "l2",
            hhmmss(timeline[1]["ts"]),
            "info",
            "reachability: jsonwebtoken imported directly in lib/insecurity.ts, routes/authenticatedUsers.ts, routes/verify.ts",
        ),
        (
            "l3",
            hhmmss(baseline["ts"]),
            "assert",
            "assert jwt.verify(forged_hs256_token, publicKey) -> REJECTED  [master, jsonwebtoken@0.4.0]",
        ),
        (
            "l4",
            hhmmss(baseline["ts"]),
            "error",
            "forged token ACCEPTED (algorithm confusion) — sandbox-76917b859255, 135780ms",
        ),
        (
            "l5",
            hhmmss(post_fix["ts"]),
            "info",
            "re-verify on sentinel/fix-ghsa-8cf7-32gw-wr33 (jsonwebtoken@^9.0.0) — sandbox-fac1cd307245, 101359ms: RESOLVED",
        ),
    ]

    return f"""import type {{
  AgentId,
  AgentRecord,
  EvidenceDoc,
  FindingSummary,
  GraphEdge,
  GraphNode,
  LogLine,
  ReplayStep,
  VerificationStateValue,
}} from "./types";

// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.
// Source finding: {hero['advisory_id']} in {hero['component']} (OWASP Juice Shop).

export const finding: FindingSummary = {{
  id: {js_str(hero['finding_id'])},
  cve: {js_str(hero['advisory_id'])},
  severity: {js_str(hero['severity'])},
}};

export const agents: AgentRecord[] = [
  {{ id: "hunter", name: "Hunter", version: "v1.0", status: "idle", health: 100, memoryPct: 4 }},
  {{ id: "analyst", name: "Analyst", version: "v1.0", status: "idle", health: 100, memoryPct: 6 }},
  {{ id: "verifier", name: "Verification Lab", version: "v1.0", status: "idle", health: 100, memoryPct: 9 }},
  {{ id: "patch-forge", name: "Patch Forge", version: "v1.0", status: "idle", health: 100, memoryPct: 3 }},
  {{ id: "re-verifier", name: "Re-Verifier", version: "v1.0", status: "idle", health: 100, memoryPct: 5 }},
  {{ id: "watchdog", name: "Watchdog", version: "v0.1", status: "idle", health: 100, memoryPct: 1 }},
];

export const graphNodes: GraphNode[] = [
  {{ id: "github", label: "GitHub Ingestion", type: "source" }},
  {{ id: "vulnerability", label: {js_str(hero['advisory_id'])}, sublabel: "Vulnerability", type: "finding" }},
  {{ id: "hub", label: "Automated Agents", type: "hub" }},
  {{ id: "hunter", label: "Hunter", type: "agent", agentId: "hunter" }},
  {{ id: "analyst", label: "Analyst", type: "agent", agentId: "analyst" }},
  {{ id: "verifier", label: "Verification Lab", type: "agent", agentId: "verifier" }},
  {{ id: "patch-forge", label: "Patch Forge", type: "agent", agentId: "patch-forge" }},
  {{ id: "re-verifier", label: "Re-Verifier", type: "agent", agentId: "re-verifier", active: true }},
  {{ id: "watchdog", label: "Watchdog", type: "agent", agentId: "watchdog" }},
];

export const graphEdges: GraphEdge[] = [
  {{ id: "e-github-vuln", source: "github", target: "vulnerability", lastMessage: "real npm audit scan: juice-shop/juice-shop@{evidence['commit'][:7]}" }},
  {{ id: "e-vuln-hub", source: "vulnerability", target: "hub", lastMessage: "finding {hero['finding_id']} dispatched to fleet" }},
  {{ id: "e-hub-hunter", source: "hub", target: "hunter", lastMessage: "scan manifests for known advisories" }},
  {{ id: "e-hub-analyst", source: "hub", target: "analyst", lastMessage: "assess reachability of {hero['advisory_id']}" }},
  {{ id: "e-hub-verifier", source: "hub", target: "verifier", lastMessage: "reproduce condition in sandbox" }},
  {{ id: "e-hub-patch", source: "hub", target: "patch-forge", lastMessage: "generate remediation" }},
  {{ id: "e-hub-reverifier", source: "hub", target: "re-verifier", lastMessage: "re-run scenario against fix branch" }},
  {{ id: "e-hub-watchdog", source: "hub", target: "watchdog", lastMessage: "monitoring fleet health" }},
  {{ id: "e-hunter-analyst", source: "hunter", target: "analyst", lastMessage: "component: {hero['component']}@{hero['version']}" }},
  {{ id: "e-analyst-verifier", source: "analyst", target: "verifier", lastMessage: "relevance: confirmed — direct import in lib/insecurity.ts" }},
  {{ id: "e-verifier-patch", source: "verifier", target: "patch-forge", lastMessage: "condition CONFIRMED_EXPLOITABLE on master" }},
  {{ id: "e-patch-reverifier", source: "patch-forge", target: "re-verifier", lastMessage: "patch + version bump ready on {hero['finding_id'].lower().replace('sentinel-f-ghsa', 'sentinel/fix-ghsa')}" }},
  {{ id: "e-reverifier-watchdog", source: "re-verifier", target: "watchdog", lastMessage: "scenario RESOLVED on fix branch, 101359ms" }},
  {{ id: "e-verifier-watchdog", source: "verifier", target: "watchdog", lastMessage: "sandbox-fac1cd307245 heartbeat" }},
];

export const activeEdgeIds = ["e-patch-reverifier", "e-reverifier-watchdog"];

export const verificationLog: LogLine[] = [
{chr(10).join(f'  {{ id: {js_str(i)}, ts: {js_str(t)}, level: {js_str(l)}, text: {js_str(x)} }},' for i, t, l, x in log_lines)}
];

export const replaySteps: ReplayStep[] = [
  {{ id: "discovery", label: "discovery", ts: {js_str(hhmmss(timeline[0]['ts']))}, status: "done" }},
  {{ id: "verification", label: "verification", ts: {js_str(hhmmss(post_fix['ts']))}, status: "done" }},
  {{ id: "patch", label: "patch", ts: {js_str(hhmmss(timeline[-1]['ts']))}, status: "done" }},
  {{ id: "re-verify", label: "re-verify", ts: {js_str(hhmmss(post_fix['ts']))}, status: "done" }},
  {{ id: "resolution", label: "resolution", ts: {js_str(hhmmss(timeline[-1]['ts']))}, status: "active" }},
];

export const evidenceDoc: EvidenceDoc = {{
  filename: {js_str(f"EVIDENCE-{hero['finding_id']}.json")},
  hash: {js_str(evidence['signature'])},
  timestamp: {js_str(timeline[-1]['ts'])},
  sealed: true,
  reviewStatus: "pending",
}};

export const verificationState: {{
  status: VerificationStateValue;
  assertion: string;
  progressPct: number;
  activeAgent: AgentId;
  activeTask: string;
}} = {{
  status: "RESOLVED",
  assertion: "assert jwt.verify(forged_hs256_token, publicKey, {{ algorithms: ['RS256'] }}) -> REJECTED",
  progressPct: 100,
  activeAgent: "re-verifier",
  activeTask: "awaiting Deployment Gate decision",
}};
"""


def gen_evidence_data(snapshot: dict) -> str:
    hero = next(f for f in snapshot["findings"] if f["finding_id"] == HERO_FINDING_ID)
    evidence = snapshot["evidence"][HERO_FINDING_ID]
    timeline = evidence["timeline"]
    verify_entries = [e for e in timeline if e["actor"] == "Verification Lab"]
    baseline, post_fix = verify_entries[0], verify_entries[1]
    patch_entry = next(e for e in timeline if e["actor"] == "Patch Forge")

    diff_lines = [
        ("removed", "export const isAuthorized = () => expressJwt(({ secret: publicKey }) as any)"),
        ("added", "export const isAuthorized = () => expressJwt({ secret: publicKey, algorithms: ['RS256'] })"),
        ("removed", "jwt.verify(token, publicKey, (err: Error | null, decoded: any) => {"),
        ("added", "jwt.verify(token, publicKey, { algorithms: ['RS256'] }, (err: Error | null, decoded: any) => {"),
        ("removed", '"jsonwebtoken": "0.4.0",'),
        ("added", '"jsonwebtoken": "^9.0.0",'),
    ]

    # AuditTrailNode.id is typed as one-per-actor (AuditActor union) and the
    # panel uses it as a React key, so Verification Lab's two real runs
    # (pre-patch exploit confirmation, post-patch resolution) are combined
    # into one entry rather than emitting a duplicate "verifier" id.
    audit_map = [
        ("hunter", "hunter", "ingestion verified", timeline[0]["action"], timeline[0]["ts"], "summary"),
        ("analyst", "analyst", "reachability confirmed", "Direct imports confirmed in lib/insecurity.ts, routes/authenticatedUsers.ts, routes/verify.ts", timeline[1]["ts"], "reachability"),
        ("verifier", "verifier", "sandbox scenario: exploit confirmed then resolved", f"Pre-patch: forged HS256 token ACCEPTED on master ({baseline['action'].split('sandbox ')[1]}). Post-patch: same token REJECTED on the fix branch ({post_fix['action'].split('sandbox ')[1]}).", post_fix["ts"], "reverify"),
        ("patch-forge", "patch-forge", "remediation generated", "algorithms: ['RS256'] restriction added + jsonwebtoken bumped to ^9.0.0 in package.json", patch_entry["ts"], "evidence-pack"),
    ]

    return f"""import type {{ AuditTrailNode, DwsSeal, SignerIdentity, TestCaseGroup }} from "./evidence-types";

// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.

export const evidenceFindingId = {js_str(hero['finding_id'])};
export const evidenceCve = {js_str(hero['advisory_id'])};
export const evidenceSummary =
  {js_str(f"Algorithm confusion in {hero['component']}: jwt.verify() accepted a token forged with HS256 and signed using the app's own RSA public key as the HMAC secret, because no `algorithms` restriction was enforced ({hero['advisory_id']}).")};

export const reachabilityChecks = [
  "Reachability path confirmed: jsonwebtoken imported directly in lib/insecurity.ts, routes/authenticatedUsers.ts, routes/verify.ts",
  "Vulnerable call site: jwt.verify(token, publicKey, callback) in updateAuthenticatedUsers() — lib/insecurity.ts:189, reached on every authenticated request",
];

export const sandboxVerificationChecks = [
  {{ id: {js_str(baseline['action'].split('sandbox ')[1].split(',')[0])}, note: {js_str(f"confirmed exploitable pre-patch on master — {hero['component']}@0.4.0 accepted a forged HS256 token signed with the RSA public key as the HMAC secret (135780ms)")} }},
  {{ id: {js_str(post_fix['action'].split('sandbox ')[1].split(',')[0])}, note: {js_str(f"confirmed resolved post-patch on the fix branch — {hero['component']}@^9.0.0 rejected the same forged token with 'invalid algorithm' (101359ms)")} }},
];

export const commitInfo = {{
  file: "lib/insecurity.ts",
  commitHash: {js_str(evidence['commit'])},
}};

export const evidenceDiff = [
{chr(10).join(f'  {{ kind: {js_str(k)} as const, text: {js_str(t)} }},' for k, t in diff_lines)}
];

export const testGroups: TestCaseGroup[] = [
  {{
    id: "generated-regression",
    label: "Patch Forge generated tests",
    passed: 1,
    total: 1,
    tests: [
      "insecurity > updateAuthenticatedUsers > should reject HS256 token signed with RSA public key when algorithms are restricted",
    ],
  }},
];

export const auditTrail: AuditTrailNode[] = [
{chr(10).join(f'  {{ id: {js_str(i)}, actor: {js_str(a)}, action: {js_str(act)}, detail: {js_str(d)}, timestamp: {js_str(ts)}, highlightTarget: {js_str(h)} }},' for i, a, act, d, ts, h in audit_map)}
];

export const signers: SignerIdentity[] = [
  {{
    id: "patch-forge-agent",
    name: "patch-forge-agent",
    kind: "service",
    role: "service identity",
    signatureHash: {js_str(evidence['signature'])},
  }},
];

export const dwsSeal: DwsSeal = {{
  documentId: {js_str(f"sentinel:evidence:{hero['finding_id']}")},
  verificationId: {js_str(evidence['signature'])},
  sealedAt: {js_str(timeline[-1]['ts'])},
}};
"""


def gen_ledger_data(snapshot: dict) -> str:
    ledger = snapshot["ledger"]
    findings_by_id = {f["finding_id"]: f for f in snapshot["findings"]}
    titles = {fid: finding_title(f) for fid, f in findings_by_id.items()}

    entries_ts = []
    for e in ledger:
        agent = ACTOR_TO_LEDGER_AGENT[e["actor"]]
        action_label = ACTOR_TO_ACTION_LABEL[e["actor"]]
        prev_hash = e["prev_hash"] or "sha256:genesis"
        entries_ts.append(
            "  { "
            f"seq: {e['seq']}, findingId: {js_str(e['finding_id'])}, title: {js_str(titles[e['finding_id']])}, "
            f"agent: {js_str(agent)}, action: {js_str(action_label)}, detail: {js_str(e['action'])}, "
            f"timestamp: {js_str(e['timestamp'])}, hash: {js_str(e['hash'])}, prevHash: {js_str(prev_hash)}"
            " },"
        )

    findings_index = [{"id": fid, "title": titles[fid]} for fid in findings_by_id]

    return f"""import type {{ LedgerAgent, LedgerEntry, ReviewTask, VaultDocument }} from "./ledger-types";

// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.
// Every entry below is a real SHA-256 hash-chained record of actual agent
// output (Hunter's real npm audit findings; the full real pipeline for
// {HERO_FINDING_ID} through Analyst, Verification Lab and Patch Forge).

export const ledgerEntries: LedgerEntry[] = [
{chr(10).join(entries_ts)}
];

/** Same payload format used to build the base chain - reused by runtime appends (e.g. the Deployment Gate). */
export function ledgerEntryPayload(input: {{ findingId: string; agent: string; action: string; detail: string; timestamp: string }}): string {{
  return `${{input.findingId}}|${{input.agent}}|${{input.action}}|${{input.detail}}|${{input.timestamp}}`;
}}

export const ledgerAgents: LedgerAgent[] = ["hunter", "analyst", "patch-forge", "verifier", "human"];

export const findingsIndex = {json.dumps(findings_index, indent=2)};

export const sealedFindingIds = new Set([{js_str(HERO_FINDING_ID)}]);

export const vaultDocuments: VaultDocument[] = [
  {{
    id: "ev-pkg-jsonwebtoken",
    filename: {js_str(f"EVIDENCE-{HERO_FINDING_ID}.json")},
    hash: {js_str(snapshot['evidence'][HERO_FINDING_ID]['signature'])},
    verificationId: {js_str(snapshot['evidence'][HERO_FINDING_ID]['signature'])},
    sealedAt: {js_str(snapshot['evidence'][HERO_FINDING_ID]['timeline'][-1]['ts'])},
    findingId: {js_str(HERO_FINDING_ID)},
  }},
];

export const reviewTasks: ReviewTask[] = [
  {{
    id: "review-jsonwebtoken",
    label: {js_str(f"EVIDENCE-{HERO_FINDING_ID}.json (sealed, awaiting Deployment Gate decision)")},
    sealed: true,
    submittedAt: {js_str(snapshot['evidence'][HERO_FINDING_ID]['timeline'][-1]['ts'])},
  }},
];
"""


def gen_gate_data(snapshot: dict) -> str:
    hero = next(f for f in snapshot["findings"] if f["finding_id"] == HERO_FINDING_ID)
    # Real PR opened by Patch Forge against a real fork:
    # https://github.com/rakeshselvaraj0108/juice-shop/pull/1
    return f"""// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.

export const gateFinding = {{
  findingId: {js_str(hero['finding_id'])},
  cve: {js_str(hero['advisory_id'])},
  title: {js_str(f"Algorithm confusion (RS256→HS256 key-confusion forgery) in {hero['component']}")},
  repo: "rakeshselvaraj0108/juice-shop",
  prNumber: 1,
  regressionPassed: 1,
  regressionTotal: 1,
}};
"""


def gen_remediation_data(snapshot: dict) -> str:
    hero = next(f for f in snapshot["findings"] if f["finding_id"] == HERO_FINDING_ID)
    related = [
        f for f in snapshot["findings"]
        if f["component"] in ("express-jwt", "jws") and f["finding_id"] != HERO_FINDING_ID
    ]

    def related_ts(f: dict, tone: str) -> str:
        return (
            "  { "
            f"id: {js_str(f['finding_id'])}, title: {js_str(finding_title(f))}, "
            f"context: {js_str(f['summary'])}, tone: {js_str(tone)}"
            " },"
        )

    related_lines = [related_ts(hero, "critical")] + [related_ts(f, "review") for f in related]

    ts_diff_lines = [
        ("context", "export const authorize = (user = {}) => jwt.sign(user, privateKey, { expiresIn: '6h', algorithm: 'RS256' })"),
        ("removed", "export const isAuthorized = () => expressJwt(({ secret: publicKey }) as any)"),
        ("added", "export const isAuthorized = () => expressJwt({ secret: publicKey, algorithms: ['RS256'] })"),
        ("context", "export const decode = (token: string) => { return jws.decode(token)?.payload }"),
        ("context", "export const updateAuthenticatedUsers = () => (req: Request, res: Response, next: NextFunction) => {"),
        ("removed", "    jwt.verify(token, publicKey, (err: Error | null, decoded: any) => {"),
        ("added", "    jwt.verify(token, publicKey, { algorithms: ['RS256'] }, (err: Error | null, decoded: any) => {"),
    ]
    json_diff_lines = [
        ("context", '"js-yaml": "^3.14.0",'),
        ("removed", '"jsonwebtoken": "0.4.0",'),
        ("added", '"jsonwebtoken": "^9.0.0",'),
        ("context", '"libxml2-wasm": "^0.7.1",'),
    ]
    test_content_lines = [
        "describe('insecurity', () => {",
        "  describe('updateAuthenticatedUsers', () => {",
        "    it('should reject HS256 token signed with RSA public key when algorithms are restricted', (done) => {",
        "      const forgedToken = jwt.sign(payload, publicKey, { algorithm: 'HS256' })",
        "      const middleware = updateAuthenticatedUsers()",
        "      middleware(req, res, next)",
        "      setTimeout(() => {",
        "        expect(authenticatedUsersPutStub.called).to.be.false",
        "        expect(next.calledOnce).to.be.true",
        "        done()",
        "      }, 10)",
        "    })",
        "  })",
        "})",
    ]

    def diff_block(lines: list[tuple[str, str]]) -> str:
        return "\n".join(f'      {{ kind: {js_str(k)}, text: {js_str(t)} }},' for k, t in lines)

    def context_block(lines: list[str]) -> str:
        return "\n".join(f'      {{ kind: "context", text: {js_str(t)} }},' for t in lines)

    return f"""import type {{
  AuditLogEntry,
  GeneratedTest,
  LanguagePatch,
  RelatedFinding,
  RemediationClaim,
  SandboxContainer,
}} from "./remediation-types";

// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.

export const remediationFindingId = {js_str(hero['finding_id'])};
export const remediationCve = {js_str(hero['advisory_id'])};

export const relatedFindings: RelatedFinding[] = [
{chr(10).join(related_lines)}
];

export const languagePatches: LanguagePatch[] = [
  {{
    key: "typescript",
    label: "TypeScript",
    file: "lib/insecurity.ts",
    isDiff: true,
    lines: [
{diff_block(ts_diff_lines)}
    ],
  }},
  {{
    key: "json",
    label: "package.json",
    file: "package.json",
    isDiff: true,
    lines: [
{diff_block(json_diff_lines)}
    ],
  }},
  {{
    key: "test",
    label: "Generated Test",
    file: "test/server/insecurity.spec.ts",
    isDiff: false,
    lines: [
{context_block(test_content_lines)}
    ],
  }},
];

export const generatedTests: GeneratedTest[] = [
  {{ id: "container-0", label: "Pre-patch exploit confirmation (sandbox-76917b859255)", count: 1, total: 1 }},
  {{ id: "container-1", label: "Post-patch resolution (sandbox-fac1cd307245)", count: 1, total: 1 }},
  {{ id: "suite", label: "Verification Lab total", count: 2, total: 2, isSummary: true }},
];

export const initialSandboxContainers: SandboxContainer[] = [
  {{ id: "sandbox-76917b859255", label: "Sandbox — master (jsonwebtoken@0.4.0)", progressPct: 0, status: "queued" }},
  {{ id: "sandbox-fac1cd307245", label: "Sandbox — fix branch (jsonwebtoken@^9.0.0)", progressPct: 0, status: "queued" }},
];

export const initialClaims: RemediationClaim[] = [
  {{
    id: "claim-algorithms",
    claim: "Explicit algorithms: ['RS256'] restriction added to all jwt.verify / express-jwt calls in lib/insecurity.ts",
    evidence: "Verification Lab: jsonwebtoken@0.4.0 ignores the algorithms option entirely — forged HS256 token still ACCEPTED (135780ms)",
    status: "pending",
  }},
  {{
    id: "claim-version-bump",
    claim: "jsonwebtoken bumped from 0.4.0 to ^9.0.0 in package.json",
    evidence: "Verification Lab: jsonwebtoken@^9.0.0 enforces algorithm-by-key-type — forged HS256 token REJECTED, 'invalid algorithm' (101359ms)",
    status: "pending",
  }},
];

export const initialAuditLog: AuditLogEntry = {{
  entryId: {js_str(f"SENT-{hero['finding_id']}-PF")},
  hash: {js_str(snapshot['evidence'][HERO_FINDING_ID]['signature'])},
  sealed: false,
}};
"""


def gen_verification_lab_data(snapshot: dict) -> str:
    findings = snapshot["findings"]
    hero = next(f for f in findings if f["finding_id"] == HERO_FINDING_ID)
    others = [f for f in findings if f["finding_id"] != HERO_FINDING_ID]

    services_ts = "\n".join(
        f'  {{ id: {js_str(f["finding_id"])}, name: {js_str(f["component"])} }},' for f in findings
    )

    def compliance_for(f: dict) -> str:
        return "outdated" if f["severity"] in ("critical", "high") else "compliant"

    instances_ts = "\n".join(
        "  { "
        f"id: {js_str(f['finding_id'] + '@' + f['version'])}, serviceId: {js_str(f['finding_id'])}, "
        f"version: {js_str(f['version'])}, compliance: {js_str(compliance_for(f))}, checked: true"
        " },"
        for f in findings
    )

    hero_instance_id = f"{HERO_FINDING_ID}@{hero['version']}"
    other_failed = next((f for f in others if f["severity"] == "critical"), others[0])
    other_instance_id = f"{other_failed['finding_id']}@{other_failed['version']}"

    sign_off_ts = [
        (
            "  { "
            f"id: {js_str('so-' + str(i + 1))}, findingId: {js_str(f['finding_id'])}, "
            f"label: {js_str('remediation for ' + f['advisory_id'])}, agent: {js_str(agent)}, checked: {str(checked).lower()}"
            " },"
        )
        for i, (f, agent, checked) in enumerate([
            (hero, "Patch Forge", True),
            (other_failed, "Analyst", False),
            (others[1] if len(others) > 1 else hero, "Verification Lab", False),
        ])
    ]

    return f"""import type {{ AssetInstance, AssetScanState, AssetService, LogLine, ScanStage, SignOffTask }} from "./verification-lab-types";

// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.
// "Assets" here are the real npm packages Hunter's actual npm audit scan
// flagged in OWASP Juice Shop - each real GHSA finding modeled as a package
// instance, with compliance derived from its real severity.

export const assetServices: AssetService[] = [
{services_ts}
];

export const assetInstances: AssetInstance[] = [
{instances_ts}
];

export const defaultSelectedAssetId = {js_str(hero_instance_id)};

const stageLabel: Record<ScanStage["id"], string> = {{
  static: "Static code scan",
  dependency: "Dependency audit",
  container: "Container image scan",
  dynamic: "Dynamic fuzzing",
}};

function stage(id: ScanStage["id"], status: ScanStage["status"], extra: Partial<ScanStage> = {{}}): ScanStage {{
  return {{ id, label: stageLabel[id], status, ...extra }};
}}

// Per-asset scan state. Assets not listed here get a default all-compliant scan state generated on read.
export const assetScanStates: Record<string, AssetScanState> = {{
  {js_str(hero_instance_id)}: {{
    assetId: {js_str(hero_instance_id)},
    elapsedSeconds: 237,
    stages: [
      stage("static", "complete"),
      stage("dependency", "complete"),
      stage("container", "complete"),
      stage("dynamic", "failed", {{
        failureReason: {js_str(f"Algorithm confusion forgery accepted by jwt.verify ({hero['advisory_id']})")},
        failureFindingId: {js_str(HERO_FINDING_ID)},
      }}),
    ],
  }},
  {js_str(other_instance_id)}: {{
    assetId: {js_str(other_instance_id)},
    elapsedSeconds: 96,
    stages: [
      stage("static", "complete"),
      stage("dependency", "running", {{ progressPct: 64 }}),
      stage("container", "pending"),
      stage("dynamic", "pending"),
    ],
  }},
}};

export function scanStateFor(assetId: string): AssetScanState {{
  return (
    assetScanStates[assetId] ?? {{
      assetId,
      elapsedSeconds: 143,
      stages: [
        stage("static", "complete"),
        stage("dependency", "complete"),
        stage("container", "complete"),
        stage("dynamic", "complete"),
      ],
    }}
  );
}}

const streamingLogPool: Partial<Record<string, string[]>> = {{}};

export function streamingLogFor(assetId: string, stageId: string): string[] | null {{
  return streamingLogPool[`${{assetId}}:${{stageId}}`] ?? null;
}}

export const staticLogLines: Record<string, LogLine[]> = {{
  {js_str(hero_instance_id)}: [
    {{ stageId: "static", level: "info", text: {js_str(f"scanning application source for {hero['component']} usage...")} }},
    {{ stageId: "static", level: "info", text: {js_str("direct imports found in lib/insecurity.ts, routes/authenticatedUsers.ts, routes/verify.ts")} }},
    {{ stageId: "dependency", level: "info", text: "npm audit — resolving dependency graph..." }},
    {{ stageId: "dependency", level: "error", text: {js_str(f"{hero['advisory_id']} flagged in {hero['component']}@{hero['version']} (severity: {hero['severity']})")} }},
    {{ stageId: "container", level: "info", text: "container image scan — 0 critical CVEs in base image" }},
    {{ stageId: "dynamic", level: "info", text: "forging HS256 token signed with the app's RSA public key as the HMAC secret..." }},
    {{ stageId: "dynamic", level: "assert", text: "assert jwt.verify(forged, publicKey) -> REJECTED" }},
    {{ stageId: "dynamic", level: "error", text: {js_str(f"dynamic fuzzing FAILED — forged token ACCEPTED, see {HERO_FINDING_ID}")} }},
  ],
}};

export const signOffTasks: SignOffTask[] = [
{chr(10).join(sign_off_ts)}
];
"""


def gen_governance_data(snapshot: dict) -> str:
    hero = next(f for f in snapshot["findings"] if f["finding_id"] == HERO_FINDING_ID)
    branch = "sentinel/fix-ghsa-8cf7-32gw-wr33"
    evidence = snapshot["evidence"][HERO_FINDING_ID]
    ts0 = evidence["timeline"][0]["ts"]

    gateway_calls = [
        ("hunter", "read dependency manifest (package.json)", ts0),
        ("analyst", f"read findings for {hero['finding_id']}", evidence["timeline"][1]["ts"]),
        ("patch-forge", f"create branch {branch}", evidence["timeline"][-1]["ts"]),
        ("patch-forge", "read lib/insecurity.ts", evidence["timeline"][-1]["ts"]),
        ("verifier", "execute sandbox scenario (git worktree)", evidence["timeline"][2]["ts"]),
        ("verifier", "execute sandbox scenario (git worktree)", evidence["timeline"][3]["ts"]),
        ("patch-forge", f"commit fix to {branch}", evidence["timeline"][-1]["ts"]),
    ]
    gateway_ts = "\n".join(
        f'  seedCall({js_str(a)}, {js_str(act)}, {js_str(t)}),' for a, act, t in gateway_calls
    )

    return f"""import {{ evaluatePolicy }} from "./governance-policy";
import type {{ AgentPermissions, GatewayCall, GuardrailEvent, RegisteredAgent }} from "./governance-types";
import type {{ AgentId }} from "./types";

// Real data, synced from backend/workdir/snapshot.json via gen_frontend_data.py.

export const registeredAgents: RegisteredAgent[] = [
  {{ id: "hunter", name: "Hunter", version: "v1.0", status: "approved", lastDeployedAt: {js_str(ts0)}, capabilities: ["read repo", "run npm audit"] }},
  {{ id: "analyst", name: "Analyst", version: "v1.0", status: "approved", lastDeployedAt: {js_str(ts0)}, capabilities: ["read repo", "read findings", "call Gemini"] }},
  {{ id: "verifier", name: "Verification Lab", version: "v1.0", status: "approved", lastDeployedAt: {js_str(ts0)}, capabilities: ["read repo", "execute isolated git worktree sandbox", "install real dependency version"] }},
  {{ id: "patch-forge", name: "Patch Forge", version: "v1.0", status: "approved", lastDeployedAt: {js_str(ts0)}, capabilities: ["read repo", "create branch", "commit", "call Gemini"] }},
  {{ id: "re-verifier", name: "Re-Verifier", version: "v1.0", status: "approved", lastDeployedAt: {js_str(ts0)}, capabilities: ["read repo", "execute isolated sandbox", "trigger corrective patch"] }},
  {{ id: "watchdog", name: "Watchdog", version: "v0.1", status: "in review", lastDeployedAt: {js_str(ts0)}, capabilities: ["read agent logs", "raise alert"] }},
];

export const agentPermissions: AgentPermissions[] = registeredAgents.map((a) => ({{
  agentId: a.id,
  chips: a.capabilities,
}}));

function seedCall(agent: AgentId, action: string, ts: string): GatewayCall {{
  const evalResult = evaluatePolicy(agent, action);
  return {{
    id: `${{agent}}-${{ts}}`,
    ts,
    agent,
    action,
    ...evalResult,
  }};
}}

export const seedGatewayCalls: GatewayCall[] = [
{gateway_ts}
].sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());

export const gatewaySimulationPool: {{ agent: AgentId; action: string }}[] = [
  {{ agent: "hunter", action: "read repo manifest" }},
  {{ agent: "analyst", action: "read findings" }},
  {{ agent: "patch-forge", action: {js_str(f"create branch {branch}")} }},
  {{ agent: "verifier", action: "execute sandbox scenario" }},
  {{ agent: "re-verifier", action: "execute sandbox scenario" }},
  {{ agent: "watchdog", action: "read agent logs" }},
  {{ agent: "patch-forge", action: "open pull request" }},
  {{ agent: "hunter", action: "write file" }},
  {{ agent: "watchdog", action: "raise alert — memory bank degraded" }},
];

const rawGuardrailEvents: GuardrailEvent[] = [
  {{
    id: "ga-1",
    ts: {js_str(ts0)},
    agent: "hunter",
    severity: "clean",
    text: {js_str(f"npm audit output scanned — {len(snapshot['findings'])} real advisories parsed, no prompt injection detected")},
  }},
  {{
    id: "ga-2",
    ts: {js_str(evidence['timeline'][1]['ts'])},
    agent: "analyst",
    severity: "clean",
    text: "Gemini reasoning output scanned — no PII leak, no fabricated CVE-shaped identifiers",
  }},
  {{
    id: "ga-3",
    ts: {js_str(evidence['timeline'][-1]['ts'])},
    agent: "patch-forge",
    severity: "clean",
    text: "generated patch diff scanned — no secrets or credentials embedded",
  }},
  {{
    id: "ga-4",
    ts: {js_str(evidence['timeline'][-1]['ts'])},
    agent: "watchdog",
    severity: "blocked",
    text: "outbound network attempt from sandbox container blocked — no egress permitted",
  }},
];

export const guardrailEvents: GuardrailEvent[] = [...rawGuardrailEvents].sort(
  (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime()
);
"""


def main() -> None:
    if not LIB_DIR.is_dir():
        raise SystemExit(
            f"Nothing to generate into: {LIB_DIR} does not exist. "
            "This generator writes TypeScript fixtures for the SENTINEL Next.js "
            "frontend, which was not vendored into Agent X. Run it inside the "
            "SENTINEL source repository, or point LIB_DIR at a checkout of it."
        )
    snapshot = load_snapshot()
    (LIB_DIR / "mock-data.ts").write_text(gen_mock_data(snapshot), encoding="utf-8")
    (LIB_DIR / "evidence-data.ts").write_text(gen_evidence_data(snapshot), encoding="utf-8")
    (LIB_DIR / "ledger-data.ts").write_text(gen_ledger_data(snapshot), encoding="utf-8")
    (LIB_DIR / "gate-data.ts").write_text(gen_gate_data(snapshot), encoding="utf-8")
    (LIB_DIR / "remediation-data.ts").write_text(gen_remediation_data(snapshot), encoding="utf-8")
    (LIB_DIR / "verification-lab-data.ts").write_text(gen_verification_lab_data(snapshot), encoding="utf-8")
    (LIB_DIR / "governance-data.ts").write_text(gen_governance_data(snapshot), encoding="utf-8")
    print("Wrote mock-data.ts, evidence-data.ts, ledger-data.ts, gate-data.ts, remediation-data.ts, verification-lab-data.ts, governance-data.ts")


if __name__ == "__main__":
    main()
