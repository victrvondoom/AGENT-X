# Contributing to Agent X

Agent X is a verifiable *right-to-be-forgotten* layer for AI-agent memory. It treats
forgetting as a first-class, provable operation rather than a best-effort cleanup — and it does
so natively inside CockroachDB, so the graph, the vectors, the proof, and the audit trail all
live in one transactional store.

We welcome contributions that keep that guarantee honest: deletion must be *complete*
(graph + vectors + derived beliefs), *provable* (before/after + irreversibility), and *safe*
(erasing one subject must never corrupt another's memory).

## Development setup

**Prerequisites**
- Python 3.11+
- A CockroachDB cluster (the free Basic tier is sufficient)
- An LLM endpoint — [Ollama](https://ollama.com) locally, or any OpenAI-compatible provider

**Install**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in DATABASE_URL, LLM, AWS
```

**Initialize the schema**
```bash
python -m db.store            # applies db/schema.sql (idempotent)
```

**Run**
```bash
uvicorn app.main:app --reload --port 8080
```

## Architecture

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Storage | `db/` | Schema, connection pool, envelope encryption, crypto-shred |
| Ingest | `core/ingest.py` | LLM entity/relationship extraction → deterministic upsert |
| Retrieval | `core/ask.py` | Vector recall + recursive-CTE graph traversal → grounded answer |
| Erasure | `core/forget.py` | ACID cascade + shared-node rule + crypto-shred + proof |
| Curation | `core/curation.py` | Stale-reference / contradiction / aging detection |
| API/UI | `app/` | FastAPI application and console |

See `docs/` for the development log, design decisions, and the CockroachDB capability tests.

## Testing

```bash
pytest                        # unit + integration tests
python scripts/smoke_tests.py # verify CockroachDB capabilities on your cluster
python evals/rrs.py           # reconstruction-robustness evaluation
```

Erasure logic is safety-critical. Any change to `core/forget.py` must ship with tests covering
the shared-node rule, cycle protection, and the before/after proof.

## Style & conventions

- Format with `ruff` / `black`; keep functions small and documented.
- Prefer explicit SQL and one ACID transaction over multi-step orchestration.
- Never log secrets, encryption keys, or decrypted content.
- Commits: imperative mood, scoped, with a short body explaining *why*.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
