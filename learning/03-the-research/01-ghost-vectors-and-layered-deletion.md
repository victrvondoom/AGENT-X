# The research: why naive deletion fails

Agent X's design is grounded in two 2026 papers on verifiable deletion.

*Naive deletion touches one layer; layered deletion removes them together:*

```mermaid
flowchart TB
  REQ["deletion request"] --> N["naive delete<br/>only the row the API knew<br/>about 18 percent robust"]
  REQ --> L["layered delete"]
  subgraph L2["remove together, about 94 percent robust"]
    D1["raw document"]
    D2["derived entities"]
    D3["edges"]
    D4["vectors"]
  end
  L --> L2
  class REQ q
  class N bad
  class L v
  class D1,D2,D3,D4 ok
  classDef q fill:#075985,stroke:#38bdf8,color:#fff
  classDef bad fill:#9d174d,stroke:#f472b6,color:#fff
  classDef v fill:#6d28d9,stroke:#a78bfa,color:#fff
  classDef ok fill:#065f46,stroke:#34d399,color:#eafff5
```

## Ghost Vectors (arXiv:2606.18497)

*"Ghost Vectors: Soft-Deleted Embeddings Remain Reconstructible in HNSW Vector
Databases."* When a RAG system honors a deletion request with a soft-delete, the
embedding stays physically recoverable from the raw index files. The authors recover
**25.5% of exact person names and 46.4% of geographic locations** from biographical
data, and up to **99% identity recovery** from facial embeddings.

Their fix — **Epoch Key Rotation**: encrypt embeddings and discard the key on deletion,
dropping PII recovery to **0%** with a cryptographic proof of deletion. This is exactly
Agent X's crypto-shred.

## ForgetAgent — layered deletion (IJRASET)

*"ForgetAgent: Verifiable Deletion in Multi-Layer Memory Architectures for LLM Agents."*
It identifies an attack surface across seven memory layers (raw text, embeddings,
summaries, derived entities, tool transcripts, neighborhoods, context) and shows that
**naive deletion is only ~18% robust** to reconstruction, while dependency-graph-aware
**layered deletion reaches ~94%**. Agent X implements the layered approach: it deletes
the document, the derived graph entities, the edges, and the vectors — together — rather
than just the row the API was told about.
