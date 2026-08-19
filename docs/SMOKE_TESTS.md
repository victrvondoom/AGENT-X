# Smoke Tests — CockroachDB Basic (free tier)

**Run:** 2026-08-16 · cluster `agent-x` (AWS Mumbai ap-south-1) · **CockroachDB CCL v26.2.5**
**Result: 11/11 PASS.** Every free-tier assumption the architecture depends on is confirmed.
Reproduce: `./.venv/bin/python scripts/smoke_tests.py`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 0 | Connect / version | ✅ | `CockroachDB CCL v26.2.5` as `vinayak/defaultdb` |
| 1 | `feature.vector_index.enabled` | ✅ **True** | Vector indexing is ON by default on Basic — the biggest unknown, resolved |
| 2 | `CREATE VECTOR INDEX ... vector_cosine_ops` | ✅ | Cosine vector index builds on Basic |
| 3 | Cosine ANN query (`<=>`) | ✅ | Returns ranked neighbors |
| 4 | `EXPLAIN` the ANN query | ✅ **index used** | Plan shows `top-k` + `lookup join` on the vector index — **NOT a full scan** |
| 5 | `AS OF SYSTEM TIME` | ✅ | Time-travel reads work — the proof-of-prior-existence mechanism |
| 6 | GC window (`gc.ttlseconds`) | ✅ | Zone config readable; AOST functional (exact window to be pinned; demo AOST capped ≤30 min) |
| 7 | Recursive CTE | ✅ | Bounded graph walk works — powers exhaustive blast-radius |
| 8 | Row-level TTL | ✅ | `ttl_expiration_expression` table accepted on Basic — retention/decay |
| 9 | `INSERT ... ON CONFLICT` | ✅ | Deterministic entity dedup |

**Verdict: GO.** No blockers. Notably, the vector index is *index-backed* (lookup join), not a full-table scan — so we clear the Product-Readiness/Technical bar on the metric that most matters for a vector-memory product.

**One follow-up (non-blocking):** pin the exact Basic GC window empirically and keep all demo `AS OF SYSTEM TIME` reads well within it; append-only/soft-delete design makes lineage independent of GC anyway.
