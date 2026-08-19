# What we measured (RRS)

We don't just cite the research — we reproduce it on Agent X.

*Same leaked ciphertext, two strategies, opposite outcomes:*

```mermaid
flowchart LR
  LK["attacker holds<br/>leaked ciphertext"] --> N["naive delete<br/>rows dropped, key kept"]
  LK --> O["Agent X<br/>cascade plus crypto-shred"]
  N --> NR["still decryptable"]
  O --> OR["0 percent, decrypt returns None"]
  class LK q
  class N,NR bad
  class O v
  class OR ok
  classDef q fill:#075985,stroke:#38bdf8,color:#fff
  classDef bad fill:#9d174d,stroke:#f472b6,color:#fff
  classDef v fill:#6d28d9,stroke:#a78bfa,color:#fff
  classDef ok fill:#065f46,stroke:#34d399,color:#eafff5
```

## Reconstruction-Robustness Score

The harness (`evals/rrs.py`) runs a leaked-ciphertext attack: ingest a corpus, then
compare two deletion strategies against an attacker who has the raw ciphertext.

| Strategy | Residual recovery from a leaked copy |
|---|---|
| **Naive delete** (drop the rows, keep the key) | data still decryptable |
| **Agent X** (cascade + crypto-shred) | **0% — key destroyed, decrypt returns None** |

The difference is the crypto-shred: naive deletion forgets to destroy the key, so a
leaked snapshot is still readable. Agent X destroys it, so it isn't.

## Forget-correctness

A second benchmark asks the agent about a subject **after** it's forgotten and checks,
with an independent blind judge, that it correctly answers *"not on record"* — while
answers about *unrelated* subjects stay correct (the forget is surgical, not lobotomy).

Everything here runs against the same CockroachDB the app uses — the numbers come from
the code as committed, not a slide.
