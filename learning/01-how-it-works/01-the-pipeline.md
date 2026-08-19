# The pipeline, end to end

Agent X stores an agent's memory as a **knowledge graph *and* vectors *and* an audit
trail — in one CockroachDB store**. Three verbs move through it:

```mermaid
flowchart LR
  I["remember<br/>ingest"] --> S[("CockroachDB<br/>documents · nodes plus VECTOR · edges<br/>subject_keys · erasure_events")]
  A["recall<br/>ask"] --> S
  F["forget<br/>erase plus prove"] --> S
  S --> C["signed WORM<br/>certificate in S3"]
  class I,A v
  class S s
  class F bad
  class C s3
  classDef v fill:#6d28d9,stroke:#a78bfa,color:#fff
  classDef s fill:#4c1d95,stroke:#c4b5fd,color:#fff
  classDef bad fill:#9d174d,stroke:#f472b6,color:#fff
  classDef s3 fill:#92400e,stroke:#fbbf24,color:#fff
```

- **remember** — a document is stored *encrypted* under its subject's key; an LLM
  extracts entities and relationships; nodes are upserted by name and edges inserted —
  all in the one store.
- **recall** — cosine ANN finds the relevant entities, a recursive CTE expands the
  surrounding graph, and a strictly-grounded prompt answers **only** from that context,
  declining honestly when a fact is absent.
- **forget** — one serializable transaction removes the subject's exclusive documents,
  nodes, edges and vectors, invalidates shared nodes, and crypto-shreds the key — then
  proves it three ways.

The honesty of **recall** is what makes **forget** provable: if the agent will say
"not on record" when it truly has nothing, then a post-forget "not on record" *means*
something.
