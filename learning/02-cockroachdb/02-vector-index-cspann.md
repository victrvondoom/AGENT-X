# Distributed vector index (C-SPANN)

Nodes carry a `VECTOR(384)` embedding (fastembed `bge-small-en-v1.5`) indexed with
CockroachDB's C-SPANN index:

*Recall is an index-backed cosine search, not a full scan:*

```mermaid
flowchart LR
  Q["query text"] --> E["fastembed<br/>VECTOR 384"]
  E --> IDX["C-SPANN index<br/>cosine distance ANN"]
  IDX --> K["top-k nodes<br/>index-backed, verified by EXPLAIN"]
  K --> S[("same table as<br/>graph plus documents")]
  class Q,E q
  class IDX v
  class K ok
  class S s
  classDef q fill:#075985,stroke:#38bdf8,color:#fff
  classDef v fill:#6d28d9,stroke:#a78bfa,color:#fff
  classDef ok fill:#065f46,stroke:#34d399,color:#eafff5
  classDef s fill:#4c1d95,stroke:#c4b5fd,color:#fff
```

```sql
CREATE VECTOR INDEX nodes_embedding_idx ON nodes (embedding vector_cosine_ops);
```

Recall is cosine ANN with the `<=>` operator:

```sql
SELECT id, weight, (embedding <=> $1) AS dist
FROM nodes
WHERE workspace = $2 AND deleted_at IS NULL AND embedding IS NOT NULL
ORDER BY embedding <=> $1
LIMIT $3;
```

The important part: this is **index-backed, not a full scan**. `EXPLAIN` on the query
shows a `top-k` + lookup join on the vector index — confirmed in our smoke tests. The
vectors live in the *same table* as the graph and the encrypted documents, so a
`forget` removes the row and its vector in the same transaction. There is no second
store to fall out of sync — which is exactly the gap the *Ghost Vectors* paper exploits
in bolt-on vector databases.
