# Submission Checklist — CockroachDB × AWS "Build with Agentic Memory"

Deadline: **Aug 18, 2026, 5:00pm EDT**. This maps every rule to our deliverable.

## Required deliverables
- [ ] **Public code repository** with an OSI license — repo `victrvondoom/AGENT-X`
      (private during build; **flip to public before submitting**). ✅ **MIT** license committed.
- [ ] **Functional demo app URL** — deployed on AWS (EC2 + S3), reachable by judges.
- [ ] **Text description** of features & functionality (Devpost fields + README).
- [ ] **Demo video < 3 minutes**, public on YouTube/Vimeo, **showing the CockroachDB memory
      layer at work** (ingest → ask → forget → 3-part proof).
- [ ] **Tool documentation** — which CockroachDB tools and AWS services we use, and how (below).
- [ ] Optional: architecture diagram (have one) + feedback on CockroachDB tooling.

## Challenge requirements
- [x] **≥ 2 CockroachDB tools, meaningfully integrated** — we use **six**:
  1. **CockroachDB Cloud Managed MCP Server** — Cockroach Labs' own hosted MCP endpoint over the cluster.
     We use it for **independent, third-party-verifiable erasure**: anyone can point an MCP agent at
     `cockroachlabs.cloud/mcp` and `select_query` the store to confirm a forgotten subject's rows are
     gone — proof that never routes through Agent X's code. Wired in `.mcp.json`; see `docs/MCP.md`.
  2. **Distributed Vector Indexing (C-SPANN)** — semantic recall + the "search returns nothing"
     erasure proof (verified index-backed, not full-scan — see `docs/SMOKE_TESTS.md`).
  3. **`AS OF SYSTEM TIME`** time-travel — the proof-of-prior-existence receipt.
  4. **Serializable transactions** — the cascade delete + invalidate + crypto-shred all commit or none do.
  5. **Recursive CTEs** — exhaustive blast-radius traversal of the knowledge graph.
  6. **Row-level TTL** — retention/decay enforcement.
  - **Also MCP-native on our side:** Agent X ships its own MCP server (FastMCP) backed by the same
    cluster, so agents can remember, recall, and provably forget through high-level tools too.
- [x] **≥ 1 AWS service** — **Amazon S3** (object-locked / WORM erasure certificates) + **EC2**
      (hosting the deployed app). Erasure certificates are signed **in-process** with ECDSA (P-256).
- [ ] **Newly created during the submission period** — new repo, new CockroachDB-native codebase.
- [ ] All materials in **English**.

## Judging criteria — how we score
| Criterion | Our answer |
|-----------|-----------|
| Agentic Memory Design | CockroachDB is the memory layer: graph + vectors + proof + TTL in one ACID store. |
| Technical Implementation | Index-backed ANN, recursive-CTE cascade, envelope encryption + crypto-shred. |
| Real-World Impact | GDPR/HIPAA right-to-erasure — a board-level blocker for regulated AI deployments. |
| Product Readiness | Auth, XSS-safe UI, tamper-evident S3 certificates, observability, tests. |
| Creativity & Originality | The only entry whose thesis is *provable forgetting*, backed by 2026 research. |

## Pre-submission gate
- [ ] Repo public + README + setup instructions + architecture diagram
- [ ] Live demo URL verified reachable
- [ ] Video uploaded (public) and linked
- [ ] Devpost form: tools used, AWS services, repo, demo, video
- [ ] `EXPLAIN` query-plan receipt + eval numbers in README
