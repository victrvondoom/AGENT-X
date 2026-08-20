<div align="center">

<img src="docs/media/logo.png" width="140" alt="Agent X logo">

# Agent X

**Agent memory that forgets on command — and proves it's gone.**

Delete it *everywhere* · in one atomic transaction · with a signed receipt anyone can check

[![▶ Try the live demo](https://img.shields.io/badge/▶_TRY_THE_LIVE_DEMO-running_on_AWS_EC2-6d28d9?style=for-the-badge&labelColor=1a1533)](https://43-204-114-100.nip.io/)

[![Live demo](https://img.shields.io/badge/Live_demo-online-22c55e?style=flat-square)](https://43-204-114-100.nip.io/)
![CockroachDB Basic](https://img.shields.io/badge/CockroachDB-Basic-6933FF?style=flat-square&logo=cockroachlabs&logoColor=white)
![Cloud MCP](https://img.shields.io/badge/Cloud_MCP-independently_verifiable-22c55e?style=flat-square)
![AWS S3 WORM](https://img.shields.io/badge/AWS_S3-Object_Lock_·_WORM-FF9900?style=flat-square&logo=amazons3&logoColor=white)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-web_+_MCP-009688?style=flat-square&logo=fastapi&logoColor=white)
![License MIT](https://img.shields.io/badge/license-MIT-8b5cf6?style=flat-square)

</div>

> **When an agent's memory is wrong, poisoned, or legally required to disappear — can you delete it _everywhere_, atomically, and _prove_ it's gone?**

Agent X can. It cascade-deletes an entity's entire knowledge sub-graph — documents, graph nodes, edges, and vectors — in **one ACID transaction**, then proves erasure three ways: an `AS OF SYSTEM TIME` before/after diff, a live vector + graph re-search that returns nothing, and a crypto-shredded, object-locked deletion certificate.

## One product, two pipelines

Agent X performs **verifiable operations on regulated data**. Whatever the operation —
remembering, processing, or erasing — it is gated by a human when uncertain, recorded on
a tamper-evident hash chain, attested by a signed certificate, and checkable by someone
who does not trust this code.

| | Erasure pipeline | Document pipeline (**TrustDoc**) |
|---|---|---|
| Question | *is this data really gone?* | *is this document really correct?* |
| Human gate | legal hold blocks `forget()` | low-confidence fields block the job |
| Self-verify | re-query the database after erasure | re-read the signed file, compare to approved |
| Certificate | erasure certificate | compliance certificate |

They are not two systems. `jobs.kind` is a **value** (`'erasure' | 'document'`), both write
to the **same `audit_log` chain**, and both are verified by the same `/verify`. Adding a
capability later means a new `kind`, not a second trust system.

```
core/trust/          audit.py · gate.py · certificate.py     ← the shared spine
core/forget.py       erasure pipeline
pipelines/document/  extract · review · generate · sign · self-verify
```

**Verify any of it without us:** `db/verify_chain.sql` re-derives every hash in raw SQL,
and `templates/verify_offline.html` checks the ECDSA signature in your browser with no
server involved — save it, disconnect, open it from disk.

Full write-up: [`docs/TRUSTDOC.md`](docs/TRUSTDOC.md) · demo script: [`DEMO.md`](DEMO.md)

Browsing, recall, the knowledge graph, and `/verify` are open to everyone. Writes are **token-gated** (an erasure product should never let anonymous visitors delete data) — to run **Forget & Prove** yourself, paste the demo token **`agent-x-judge-75a0f127`** in **Settings → Security**.

<div align="center">

<br>

![Agent X landing](docs/screenshots/01-landing-hero.png)

<br>

*Living memory — the knowledge graph with real-time physics (47 entities · 83 relationships, all in CockroachDB):*

![Agent X knowledge graph — live physics](docs/media/graph.gif)

</div>

---

## The problem

Every agentic-memory project this cycle answers the same question: *how do agents remember more?* Almost none answer the question that governed, production memory actually demands: **how do agents forget — completely, safely, and provably?**

Memory that only ever accumulates is a liability. A poisoned or wrong fact propagates through the knowledge graph and corrupts future reasoning. A departed customer's data lingers past its legal retention window (EU GDPR Article 17 "right to erasure" is a 2026 enforcement priority). And in most systems, "delete" is a best-effort `DELETE` that leaves recoverable vectors on disk, orphaned graph edges, and no proof anything happened.

## What Agent X does

Point it at an entity — a customer, a decommissioned system, a poisoned memory — and it performs **verifiable erasure**:

| Stage | Guarantee |
|-------|-----------|
| **Cascade delete** | The entity's documents, graph nodes, edges, and vectors are removed in **one serializable transaction** — never a half-erased state. |
| **Shared-node safety** | Entities shared with a *surviving* subject are **invalidated, not deleted** — erasing one subject never corrupts another's memory. |
| **Crypto-shred** | The subject's per-record encryption key is destroyed, so residual ciphertext (in MVCC history, backups, or S3) is **cryptographically unrecoverable** — not merely dereferenced. |
| **Proof of prior existence** | `AS OF SYSTEM TIME` reconstructs exactly what the graph knew *before* erasure — the database's own memory of itself, no separate audit log. |
| **Proof of absence** | A live vector + graph re-search returns nothing; the agent answers *"not on record."* |
| **Certificate** | A signed, **object-locked (WORM) S3** certificate makes each erasure tamper-evident. |

Two applications of the same primitive:
- **Data-integrity / incident response** — a poisoned or wrong fact entered your agent's memory. Cut it out cleanly, cluster-wide, and prove the graph is clean again.
- **Compliance** — GDPR/HIPAA right-to-erasure with a certificate you can hand an auditor.

### Grounded recall — with sources, and honest about absence

Ask in plain English. Answers are grounded **strictly** in the stored graph, cite their sources, and decline honestly when a fact isn't on record — the exact behavior that makes forgetting provable.

![Agent X chat — grounded answer with sources](docs/media/chat.gif)

### Forget & Prove — the hero

One click erases a subject in a single ACID transaction, then proves it three ways — *it existed* (`AS OF SYSTEM TIME`), *it's gone* (live vector + graph re-check), *it's irreversible* (crypto-shred + object-locked S3 certificate). Entities shared with a *surviving* subject are **kept, not deleted** (note the **1 shared kept**).

![Forget &amp; Prove — the live 3-part proof of erasure](docs/media/forget-prove.gif)

It produces a signed, object-locked **Certificate of Erasure** — and **anyone can independently re-check it** at **`/verify`**: the page re-derives the SHA-256 content hash and checks the ECDSA (P-256) signature (public key shown, so it verifies offline). A tampered field breaks the hash; a forged certificate fails the signature.

| Certificate of Erasure | Independent verifier (`/verify`) |
|:---:|:---:|
| <img src="docs/screenshots/07-certificate.png" width="380" alt="Certificate of Erasure"> | <img src="docs/screenshots/09-verify.png" width="380" alt="Certificate verifier"> |

**Don't take our word for it — here's the evidence, outside the app:**

*The certificate object in the AWS S3 console: **Object Lock retention — Compliance mode** (WORM: not even the account root can delete or overwrite it before expiry):*

![S3 Object Lock — Compliance mode on an erasure certificate](docs/screenshots/10-s3-object-lock.png)

*And an independent audit — direct SQL on the CockroachDB cluster (no application in the loop) for a previously forgotten subject: **0 nodes · 0 documents · data key destroyed**:*

![Independent audit — direct SQL, zero rows for the forgotten subject](docs/screenshots/11-independent-audit.png)

*The same check through **CockroachDB Cloud's Managed MCP server** — an auditor queries the cluster directly (not our app) and gets **0 rows** for the forgotten subject:*

![Managed MCP audit — 0 rows for the forgotten subject](docs/screenshots/12-mcp-audit.png)

The knowledge graph (47 entities · 83 relationships, live physics) and the in-app docs:

| Knowledge graph | Docs (`/learn`) |
|:---:|:---:|
| ![Knowledge graph](docs/screenshots/02-graph.png) | ![Docs](docs/screenshots/08-docs.png) |

## Why CockroachDB (load-bearing, not a checkbox)

Agent X unifies what normally takes three systems — a graph database, a vector store, and an audit log — into **one durable, governed store**. That is only possible because of CockroachDB primitives:

- **Distributed Vector Indexing (C-SPANN)** — semantic recall over `VECTOR(384)` columns, *index-backed* (verified via `EXPLAIN`), living in the same table as the relational data.
- **`AS OF SYSTEM TIME`** — MVCC time-travel *is* the deletion receipt. No bolt-on history table to trust.
- **Serializable transactions** — the cascade delete + invalidate + crypto-shred either all commit or none do.
- **Recursive CTEs** — exhaustive, by-construction blast-radius traversal of the knowledge graph.
- **Row-level TTL** — retention enforced by the storage engine. Opt-in per row (`ttl_expire_at`): documents expire only when a retention policy sets it, so nothing is deleted by a blanket clock.
- **Managed MCP Server (independent verification)** — Agent X wires CockroachDB Cloud's **own managed MCP endpoint** (`cockroachlabs.cloud/mcp`). This is the strongest form of the erasure claim: an auditor doesn't have to trust *our* API when it says "it's gone" — they point their own MCP agent at Cockroach Labs' hosted endpoint and `select_query` the cluster directly to confirm the forgotten subject's rows are truly absent. Proof that never routes through Agent X's code. See [`docs/MCP.md`](docs/MCP.md).
- **MCP-native (our own tools)** — Agent X *also* ships its own MCP server (FastMCP) backed by the same cluster, so any MCP agent (Claude Desktop/Code, Cursor) can remember, recall, and *provably forget* through high-level tools.

**CockroachDB tools used (load-bearing):** CockroachDB Cloud Managed MCP Server · Distributed Vector Indexing (C-SPANN) · `AS OF SYSTEM TIME` · Serializable transactions · Recursive CTEs · Row-level TTL.
**AWS services used:** S3 (object-locked / WORM erasure certificates) · EC2 (hosting). Certificates are signed **in-process** with ECDSA (P-256); a Lambda-based signer is an optional deployment variant, not required.

## Architecture

```mermaid
flowchart TB
    U["User / Agent"] -->|HTTPS| API["FastAPI app"]
    MCP["Agent X MCP server (FastMCP)"] -->|"remember · recall · forget (via core)"| CRDB
    API -->|"vectors · AS OF SYSTEM TIME · cascade · CTEs"| CRDB
    subgraph CRDB["CockroachDB (one transactional store)"]
      D["documents (encrypted)"]
      N["nodes + VECTOR index"]
      E["edges (graph)"]
      K["subject_keys (crypto-shred)"]
      EV["erasure_events (audit)"]
    end
    API -->|"sign (ECDSA P-256) + PUT (Object Lock / WORM)"| S3["Amazon S3 — erasure certificates"]
    API -->|"embeddings"| FE["fastembed (local, 384-d)"]
    API -->|"generation"| LLM["LLM — local (Ollama) or hosted, BYO-model"]
```

## How it works

- **Ingest** — a document is stored encrypted under its subject's key; an LLM extracts entities and relationships; nodes are upserted by name (`INSERT … ON CONFLICT` = deterministic coreference dedup) and edges inserted — all in the one store.
- **Ask** — cosine ANN finds the relevant entities, a recursive CTE expands the surrounding graph, and a strictly-grounded prompt answers **only** from that context, declining honestly when a fact is absent (the property that makes forgetting provable).
- **Forget** — the transaction described above.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL, LLM provider, AWS
python scripts/init_db.py     # apply schema + self-test
uvicorn app.main:app --port 8080
```

**Bring your own model.** The LLM layer is provider-agnostic — point it at a local Ollama model or any hosted OpenAI-compatible provider by editing `.env` (or from the in-app model picker). No provider lock-in.

## Evaluation

Agent X implements research-backed *layered* deletion rather than naive `DELETE`:

- Naive deletion is only **~18%** robust to reconstruction attacks; dependency-graph-aware layered deletion reaches **~94%** (*ForgetAgent*, IJRASET).
- API-confirmed vector deletion leaves embeddings physically recoverable from the raw index on disk — *Ghost Vectors* (arXiv:2606.18497) reconstructs **25.5% of exact names and 46.4% of locations** from text embeddings (and up to ~99% from image embeddings); crypto-shredding a per-subject key drops recovery to **0%** (the paper's own "Epoch Key Rotation" fix — encrypt, then discard the key).

The eval harness (`evals/`) reproduces a forget-correctness benchmark (blind-judge scored) and a Reconstruction-Robustness Score comparing naive deletion vs. Agent X.

## Built for this hackathon

Agent X's CockroachDB-native memory engine was **built new for this hackathon** — the knowledge graph as
relational tables, vectors in the C-SPANN index, the atomic transactional `forget`, the `AS OF SYSTEM TIME`
proof, the per-subject crypto-shred, and the signed erasure certificate. Unifying the graph, the vectors,
and the audit trail into **one CockroachDB store** is precisely what makes forgetting a **single ACID
transaction** and the certificate provable from the database itself — something impossible when the graph
and vectors live in two separate stores. That single-store design is the structural core: a **single ACID
cascade**, **`AS OF SYSTEM TIME`** as the proof mechanism, **object-locked crypto-shred certificates**, and
an **exhaustive recursive-CTE blast-radius**.

## Honest limitations

- Erasure removes data from the store; it does not unlearn an LLM's parametric priors (which is why the grounding prompt is strict and honesty is verified behaviorally).
- Coreference is name-based; entities that should be distinct can merge and vice-versa.
- The `AS OF SYSTEM TIME` window is bounded by the cluster GC window; the append-only audit trail and S3 certificate provide durability beyond it.

## License

[MIT](LICENSE) © 2026 Vinayak Sonthalia
