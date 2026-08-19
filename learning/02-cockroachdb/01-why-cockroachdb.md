# Why CockroachDB (load-bearing, not a checkbox)

Agent X unifies what normally takes **three systems** — a graph database, a vector
store, and an audit log — into **one durable, governed store**. That is only possible
because of specific CockroachDB primitives:

*Three bolt-on systems collapse into one transactional store:*

```mermaid
flowchart LR
  subgraph OLD["the usual bolt-on stack"]
    G1["graph database"]
    V1["vector store"]
    A1["audit log"]
  end
  OLD --> X["three systems<br/>drift out of sync"]
  ONE[("one CockroachDB store<br/>graph plus VECTOR plus audit trail<br/>one ACID transaction")]
  class G1,V1,A1 q
  class X bad
  class ONE s
  classDef q fill:#075985,stroke:#38bdf8,color:#fff
  classDef bad fill:#9d174d,stroke:#f472b6,color:#fff
  classDef s fill:#4c1d95,stroke:#c4b5fd,color:#fff
```

| Capability | What it does for Agent X |
|---|---|
| **Distributed Vector Index (C-SPANN)** | Semantic recall over `VECTOR(384)` columns, *index-backed* (verified via `EXPLAIN`), in the same table as the relational data. |
| **`AS OF SYSTEM TIME`** | MVCC time-travel *is* the deletion receipt. No bolt-on history table to trust. |
| **Serializable transactions** | The cascade delete + invalidate + crypto-shred either all commit or none do. |
| **Recursive CTEs** | Exhaustive, by-construction blast-radius traversal of the graph. |
| **Row-level TTL** | Retention enforced by the storage engine. |

**Would it collapse on Postgres?** The idea does. Postgres has no `AS OF SYSTEM TIME`
(so the proof-of-prior-existence would need a separate, trust-me audit table), and no
distributed C-SPANN vector index. Moving to CockroachDB is precisely what makes
forgetting a **single ACID transaction** and the certificate **provable from the
database itself**.
