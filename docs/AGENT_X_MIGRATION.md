# What's new: the consumer resolution engine

This document is for someone who already runs Agent X and wants to know exactly
what showed up: a new capability — consumer problem resolution — built on top of
the existing trust spine, additively. Nothing in the erasure pipeline, the
document pipeline, the trust spine, or the existing API surface was removed,
renamed, or had its behaviour changed. Every route, script and test that worked
before this addition works identically after it.

## What is new

### Code

```
agentx/                              the consumer resolution engine (new)
app/agentx_api.py                    its HTTP router (new)
templates/agentx.html                the consumer workspace UI, served at /agentx (new)
db/migrations/005_agentx_cases.sql   the case layer schema (new)
db/migrations/006_sandbox.sql        the sandbox companies' schema (new)
db/migrations/007_outcomes.sql       cross-case outcome memory (new)
tests/test_agentx_*.py               its test suite (new, 14 files, 230 tests)
docs/AGENT_X_ARCHITECTURE.md         architecture (new, this document's companion)
docs/AGENT_X_MIGRATION.md            this file (new)
```

### Two lines changed in `app/main.py`

```python
from app.agentx_api import router as agentx_router
app.include_router(agentx_router)
```

mounted the same way `app/trustdoc.py`'s router already was, plus one new page
route (`GET /agentx`) alongside the existing `/app`, `/trustdoc`, `/spine` routes.
Nothing else in `app/main.py` was touched.

### Nothing else in the existing tree was modified, with one exception

`core/`, `db/store.py`, `db/schema.sql`, `pipelines/`, `aws/certificate.py`,
`llm/client.py`, and every existing template are byte-for-byte what they were
before this addition. `db/migrations/001`–`004` (the trust spine) are untouched.

The exception is `mcp_server.py`: its existing `remember`/`recall`/`forget`/
legal-hold tools are unchanged, and eleven new tools were appended below them —
`open_case`, `case_status`, `list_cases`, `attach_evidence`,
`list_evidence_kinds`, `answer_case_question`, `advance_case`,
`approve_case_action`, `case_receipt`, `forget_case`, `what_worked_before` —
thin wrappers around
`agentx/engine.py` and `agentx/case.py`, exposing the same governed pipeline
`/agentx` drives to any MCP client (Claude Desktop, Claude Code, Cursor). See
`docs/AGENT_X_ARCHITECTURE.md`'s MCP section for what makes an LLM-driven caller
a different kind of client to design for than a UI's fetch calls.

## Database changes

Two new migration files, applied by `agentx/store.py:ensure_schema()` — a
different code path from `db/store.py:apply_schema()`, which still applies
`db/schema.sql` exactly as before via `scripts/init_db.py`.

| File | Adds |
|---|---|
| `005_agentx_cases.sql` | `cases`, `case_interpretations`, `case_entities`, `evidence_items`, `evidence_facts`, `evidence_links`, `contradictions`, `case_policies`, `remedies`, `plans`, `plan_steps`, `authorizations`, `executions`, `communications`, `deadlines`, `followups`, `case_chain`, `receipts`, `case_questions` |
| `006_sandbox.sql` | `sandbox_objects`, `sandbox_clock` |
| `007_outcomes.sql` | `case_outcomes` (structural, no-PII cross-case learning), plus `plans.prior` |

Both are written in a portable SQL subset (no CockroachDB-only syntax, no
`gen_random_uuid()` defaults, no `now()` defaults — every id and timestamp comes
from the application) so the same DDL applies unchanged to CockroachDB *or* a
local SQLite file. They run automatically on first request to the new engine;
there is no separate init step to remember, though `scripts/init_db.py` can be
extended to call `agentx.store.ensure_schema()` explicitly for a CockroachDB
deployment that wants schema applied at deploy time rather than at first request.

**No column was added to, removed from, or altered on any existing table.** The
trust spine's `jobs`/`fields`/`audit_log`/`certificates` and the memory engine's
`documents`/`nodes`/`edges`/`subject_keys`/`erasure_events`/`workspaces`/
`timeline` are exactly as `db/schema.sql` and `db/migrations/001`–`004` leave
them. The one point of contact is additive: `cases.job_id` optionally references
a `jobs.id` row opened via `core.trust.pipeline_job.open_job` (unmodified) so a
case's audit trail can ride the same spine as a full subject erasure, when
CockroachDB is the live engine.

## API changes

All additions live under `/api/agentx/*`. No existing route's path, method,
request shape, or response shape changed. See `docs/AGENT_X_ARCHITECTURE.md` or
`GET /api/agentx/health` (which lists the mounted action vocabulary and autonomy
levels) for the full surface; the shape follows the case —
`/api/agentx/cases/{id}/…` for nearly everything, and `GET /api/agentx/cases/{id}`
returns a complete case snapshot in one call.

Auth: writes are gated by the same `AGENT_X_AUTH_TOKEN` Bearer-token check the
rest of the app already uses (`require_auth` in `app/agentx_api.py` mirrors the
one in `app/main.py`). Reads — ontology, capabilities, providers, governor
policy, the public key, `/understand` — are public, on the same principle
already applied to `/api/ask` and `/verify`: a system that claims to be
inspectable has to be inspectable without credentials.

## New modules, by responsibility

See `docs/AGENT_X_ARCHITECTURE.md`'s module map for the full tree with
descriptions. In one line each:

- `agentx/ontology/` — the declarative problem catalogue (28 types, 14 domains)
- `agentx/understanding.py` — narrative → posterior distribution, not a label
- `agentx/policy.py` — deterministic rights evaluation against a YAML corpus
- `agentx/evidence/` — extraction, fact graph, contradiction detection, packages
- `agentx/eligibility.py` — policy findings → ranked, costed remedies
- `agentx/planner.py` — plan composition + the deterministic validator
- `agentx/capabilities.py` — the capability registry
- `agentx/governor.py` — the Risk & Autonomy Governor
- `agentx/execution/` — the action vocabulary, provider interface, the runner
- `agentx/sandbox/` — five deterministic sandbox companies
- `agentx/followup.py` — the case-aware follow-up scheduler
- `agentx/case.py` — the Case abstraction and its state machine
- `agentx/chain.py`, `agentx/sealing.py` — per-case trust primitives, reusing the spine's
- `agentx/receipt.py`, `agentx/letters.py` — the signed receipt, grounded letters
- `agentx/demo.py` — the five end-to-end scenarios
- `agentx/store.py` — the CockroachDB/SQLite portable persistence layer
- `agentx/outcomes.py` — cross-case outcome memory: what worked against which counterparty, stored as structure so it survives erasure
- `agentx/execution/providers/live_providers.py` — `LiveEmailProvider`, a real SMTP integration behind the same interface the sandbox uses, self-registering only when configured

## Removed or replaced modules

**Deleted:** `core/booking_inspector.py`, `core/classifier.py`,
`core/case_tracker.py`, and `tests/test_v1_modules.py`.

These were early keyword-heuristic prototypes of what `agentx/understanding.py`,
`agentx/policy.py` and `agentx/case.py` now do properly — Bayesian hypothesis
tracking with expected-information-gain questions; a cited, deterministic rights
engine; a first-class Case with an enforced state machine and its own trust
chain. The three legacy routes that used to call them (`/api/inspect_booking`,
`/api/classify_intent`, `/api/cases`) now delegate to the real engine: same
paths, same response shapes, because the existing console UI calls them — real
chained cases behind them.

`/api/cases` is the one that mattered. It used to create a parallel, weaker case
with no audit chain, no receipt and no crypto-shred: two case systems in one
codebase, and the one a consumer could reach was the one with no proof. There is
now one. Once nothing imported the prototypes, keeping them was worse than
useless — a passing test suite around a heuristic classifier reads as though it
is still part of the product. `tests/test_agentx_legacy_routes.py` replaces
those tests and asserts the routes behave like the engine, including that an
ordinary coffee receipt is **not** dispute-eligible (the prototype appended a
filler "anomaly" when it found none, so it recommended a chargeback for
everything).

## Migrations

`agentx/store.py` applies `005_agentx_cases.sql`, `006_sandbox.sql`,
`007_outcomes.sql` and `008_case_clock.sql` in order, on whichever engine is
live. They are written in the portable subset documented in 005, so the same
files run against CockroachDB and SQLite. Migrations 001–004 belong to the trust
spine and are applied by `scripts/init_trust.py`.

`008_case_clock.sql` adds `cases.opened_offset_days`: where a case started on
the clock it lives on. It is always 0 for a live case. It exists because
`days_to_close` — which `outcomes.prior_for()` averages into the `typical_days`
figure shown to a user deciding whether a claim is worth chasing — was being
computed by subtracting two stamps taken from *different* clocks, so a sandbox
case that chased twice and escalated once across seven simulated days recorded
0.0 days. See "Time" below.

## Time

Two clocks exist and they must never be mixed in one subtraction.

* **Wall clock** — `ids.now()`. Every live case runs entirely on it.
* **Sandbox clock** — `sandbox_clock.offset_days`, movable via
  `world.advance()`, so a seven-day chase happens in a second.

`followups.fired_at` is now stamped with the clock the sweep ran on (`as_of`),
not with `ids.now()`, so the record of *when* a follow-up happened survives.
`cases.opened_offset_days` records where the case started, so elapsed time is a
subtraction between two points on the same clock rather than a measure of the
clock's total displacement. For a live case both reduce to plain wall-clock
arithmetic.

## Environment variables

No existing variable's meaning changed. These are newly *consulted* (never
required):

| Variable | Effect | Falls back to |
|---|---|---|
| `AGENT_X_ENGINE` | force `cockroachdb` or `sqlite` | auto-detect via `DATABASE_URL` reachability |
| `AGENT_X_DB_PATH` | local SQLite file location | `data/agentx.db` |
| `AGENT_X_SANDBOX` | set `0` to disable sandbox provider registration | `1` (sandbox providers on) |
| `AGENT_X_SMTP_HOST`/`_PORT`/`_USER`/`_PASSWORD`/`_FROM`/`_TLS` | set all five (host/user/password/from required; port and TLS have defaults) to register the real `live:smtp` email provider | unregistered — the sandbox mailbox handles `email` actions instead |

Two existing variables are reused, not redefined:

| Variable | Existing purpose | New purpose |
|---|---|---|
| `AGENT_X_ROOT_KEY` | wraps every subject's data key | also wraps every case's data key |
| `AGENT_X_SIGNING_KEY` | signs erasure and compliance certificates | also signs resolution receipts and evidence packages |

Both fall back to a mint-once local keyfile beside the SQLite database when
unset, so the new engine is runnable out of the box with zero configuration —
`git clone && pip install -r requirements.txt && uvicorn app.main:app` is enough
to open a case, run a demo scenario, and get a signed receipt. A production
deployment should still set both variables explicitly, exactly as `.env.example`
already instructs for the rest of Agent X.

## Running it

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8080
# open http://localhost:8080/agentx
```

No `DATABASE_URL` is required. If one is set and reachable, it uses CockroachDB
automatically and gets `AS OF SYSTEM TIME` proofs and vector recall for free; if
not, it runs entirely on a local SQLite file with the same trust guarantees minus
those two CockroachDB-specific ones (see `GET /api/agentx/health` →
`engine.not_available` for the exact, honest list at runtime).

To exercise the whole pipeline without touching the UI:

```bash
curl -X POST localhost:8080/api/agentx/demo/reset -H "Authorization: Bearer $AGENT_X_AUTH_TOKEN"
curl -X POST localhost:8080/api/agentx/demo/run/A -H "Authorization: Bearer $AGENT_X_AUTH_TOKEN"
```

## Test suite

```bash
pytest tests/ -v                          # everything, including the erasure/document pipelines
pytest tests/test_agentx_*.py -v          # the resolution engine only, no DATABASE_URL needed
```

These tests run entirely against the local SQLite engine (`store.reset_for_tests()`
per test) and never require CockroachDB, an LLM, or network access — including
the five end-to-end demo scenarios in `test_agentx_demo.py`, each of which drives
the real pipeline (classification → evidence → policy → planning → governed
execution → verification → signed receipt) against the deterministic sandbox.
