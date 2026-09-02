/**
 * The fleet topology, shared by the 3D scene, the hover tooltips and the
 * agent detail panel.
 *
 * This is deliberately one source of truth rather than positions hardcoded
 * in the scene and copy hardcoded in the overlay: the whole point of the
 * landing scene is that it is a literal diagram of the product, so if the
 * pipeline order here ever drifts from the real pipeline in
 * backend/app/worker.py, the scene stops being an explanation and starts
 * being decoration.
 *
 * Roles, scopes and tools mirror backend/app/governance/identity.py.
 */

export type NodeKind = "source" | "agent" | "sink";

export type GeometryKind =
  | "box"
  | "icosahedron"
  | "octahedron"
  | "tetrahedron"
  | "dodecahedron"
  | "cone"
  | "cylinder";

export interface FleetNode {
  id: string;
  label: string;
  kind: NodeKind;
  /** One line, shown on hover. Present tense, what it does. */
  responsibility: string;
  /** Longer copy for the click-through panel. */
  detail: string;
  /** Least-privilege scopes, mirroring the Agent Identity registry. */
  scopes: string[];
  /** Tools this agent is permitted to call through the Agent Gateway. */
  tools: string[];
  geometry: GeometryKind;
  /** World position. The chain reads left to right with depth variance so
   *  the constellation has parallax rather than sitting on a flat plane. */
  position: [number, number, number];
  /** Base radius; the active node scales up from this. */
  size: number;
}

export const FLEET: FleetNode[] = [
  {
    id: "github",
    label: "GitHub",
    kind: "source",
    responsibility: "Source repository under continuous watch",
    detail:
      "The target codebase. SENTINEL clones it shallowly and re-scans on demand - nothing is read from a cached index or a fixture.",
    scopes: ["repo:read"],
    tools: ["git clone", "git worktree"],
    geometry: "box",
    position: [-3.3, 0.25, -1.9],
    size: 0.46,
  },
  {
    id: "hunter",
    label: "Hunter",
    kind: "agent",
    responsibility: "Runs the real scanner and grounds every finding",
    detail:
      "Executes npm audit against the cloned repo, then resolves every advisory ID against OSV, NVD and GHSA. A finding that cannot be grounded in a real published record never reaches Analyst.",
    scopes: ["repo:read", "knowledge:read"],
    tools: ["npm audit", "lookup_vulnerability", "osv.dev", "nvd", "ghsa"],
    geometry: "icosahedron",
    position: [-2.25, 1.55, 0.7],
    size: 0.5,
  },
  {
    id: "analyst",
    label: "Analyst",
    kind: "agent",
    responsibility: "Reasons about whether the flaw is reachable here",
    detail:
      "Traces import paths and call sites with Gemini 3.6 Flash to form a reachability hypothesis. It proposes a verdict - it is never allowed to be the thing that confirms one.",
    scopes: ["repo:read", "memory:read"],
    tools: ["trace_reachability", "search_memory_bank", "gemini"],
    geometry: "octahedron",
    position: [-1.15, -1.45, -0.45],
    size: 0.5,
  },
  {
    id: "verifier",
    label: "Verification Lab",
    kind: "agent",
    responsibility: "Proves exploitability by actually running it",
    detail:
      "Clones the repo into an isolated git worktree and executes a real exploit attempt. A finding is only marked exploitable if the exploit genuinely worked - this is where a claim becomes evidence.",
    scopes: ["sandbox:execute", "repo:read"],
    tools: ["run_in_sandbox", "git worktree", "npm test"],
    geometry: "tetrahedron",
    position: [0.0, 1.35, 1.5],
    size: 0.58,
  },
  {
    id: "patch_forge",
    label: "Patch Forge",
    kind: "agent",
    responsibility: "Writes the fix from a grounded OWASP pattern",
    detail:
      "Generates a patch from a catalogued remediation pattern for that CWE. If no pattern exists it escalates to a human rather than improvising a fix - refusing is a valid, expected outcome.",
    scopes: ["repo:write", "knowledge:read", "memory:read"],
    tools: ["retrieve_fix_pattern", "propose_patch", "gemini"],
    geometry: "dodecahedron",
    position: [1.15, -1.55, -0.7],
    size: 0.52,
  },
  {
    id: "re_verifier",
    label: "Re-Verifier",
    kind: "agent",
    responsibility: "Re-runs the exploit against the patched build",
    detail:
      "Applies the patch in the sandbox and runs the same exploit again. The fix is only accepted if the attack that previously succeeded now fails, and the existing test suite still passes.",
    scopes: ["sandbox:execute", "repo:read"],
    tools: ["run_in_sandbox", "npm test"],
    geometry: "cone",
    position: [2.25, 1.25, 0.55],
    size: 0.5,
  },
  {
    id: "evidence",
    label: "Evidence Agent",
    kind: "agent",
    responsibility: "Signs and seals the record",
    detail:
      "Assembles the full chain of reasoning into one record, signs it with SHA-256, and seals a CAdES-signed PDF through Nutrient DWS. Two independent seals, so tampering with either the JSON or the PDF is detectable.",
    scopes: ["evidence:write", "sign:invoke"],
    tools: ["assemble_evidence", "nutrient_dws", "sha256"],
    geometry: "cylinder",
    position: [3.2, -0.55, -1.25],
    size: 0.5,
  },
  {
    id: "vault",
    label: "Evidence Vault",
    kind: "sink",
    responsibility: "Tamper-evident store on Cloud Firestore",
    detail:
      "Sealed records land in Cloud Firestore behind a SHA-256 hash-chained ledger. Any edit to a record or a link in the chain breaks verification.",
    scopes: ["evidence:read"],
    tools: ["firestore", "hash-chain ledger"],
    geometry: "box",
    position: [4.05, 0.85, 0.95],
    size: 0.46,
  },
];

/** Consecutive pairs, i.e. the edges a finding travels along. */
export const EDGES: Array<[number, number]> = FLEET.slice(0, -1).map((_, i) => [i, i + 1]);

/** Index of the node the "Get started" camera flies toward. */
export const FOCUS_INDEX = FLEET.findIndex((n) => n.id === "verifier");

export const COLORS = {
  bg: "#0A0C10",
  amber: "#F2A63C",
  idle: "#6B7280",
  verified: "#4ADE80",
  edge: "#2A2E38",
  text: "#E6E8EC",
} as const;
