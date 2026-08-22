-- ═══════════════════════════════════════════════════════════════════════════
-- 005_agentx_cases.sql — the Case spine
--
-- Agent X's core abstraction is the CASE: one consumer problem, tracked from the
-- sentence the user typed to a verified outcome. Everything below hangs off it.
--
-- Two deliberate portability choices, both engineering decisions rather than
-- compromises:
--
--   * JSON payloads are TEXT, not JSONB. The trust spine already learned this the
--     hard way (see 002_canonical_detail.sql): a database renders JSONB its own
--     way, so the bytes that go in are not the bytes that come out, and any hash
--     over them is unreproducible. Agent X hashes case state into a chain, so the
--     stored bytes must BE the canonical bytes. TEXT is the only column type that
--     guarantees that.
--
--   * Timestamps are ISO-8601 UTC strings written into TIMESTAMPTZ columns.
--     CockroachDB casts them on the way in and compares them as instants; SQLite
--     (the local fallback engine) compares them lexicographically, which for
--     ISO-8601 UTC is the same ordering. One DDL, two engines, no dialect fork.
--
-- Nothing here uses a function default (gen_random_uuid(), now()). IDs and clocks
-- come from the application so that a case written on one engine reads identically
-- on the other, and so a case's identifiers are stable inside a hash chain.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── cases ─────────────────────────────────────────────────────────────────
-- One consumer problem. `state` is the case state machine; `autonomy_level` is
-- the ceiling the user granted for this case, which the governor may lower for a
-- given action but never raise.
CREATE TABLE IF NOT EXISTS cases (
    id              TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL DEFAULT 'default',
    user_ref        TEXT NOT NULL,
    title           TEXT,
    description     TEXT NOT NULL,
    domain          TEXT,
    problem_type    TEXT,
    confidence      DOUBLE PRECISION,
    state           TEXT NOT NULL DEFAULT 'OPEN',
    autonomy_level  INT NOT NULL DEFAULT 2,
    risk            TEXT,
    resolution      TEXT,
    outcome_summary TEXT,
    amount_minor    BIGINT,
    currency        TEXT,
    job_id          TEXT,
    subject         TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS cases_ws_state_idx ON cases (workspace, state);
CREATE INDEX IF NOT EXISTS cases_user_idx     ON cases (workspace, user_ref);

-- ── interpretations ───────────────────────────────────────────────────────
-- "They charged me again" has at least six meanings. Committing to one before the
-- evidence supports it is the most common failure mode of a consumer agent, so
-- Agent X keeps every live hypothesis as a row and collapses only when evidence
-- separates them. `status` records how a hypothesis died, which is what makes the
-- reasoning auditable rather than merely plausible.
CREATE TABLE IF NOT EXISTS case_interpretations (
    id              TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    domain          TEXT NOT NULL,
    problem_type    TEXT NOT NULL,
    prior           DOUBLE PRECISION NOT NULL,
    posterior       DOUBLE PRECISION NOT NULL,
    status          TEXT NOT NULL DEFAULT 'LIVE',
    rationale       TEXT,
    discriminators  TEXT,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS interp_case_idx ON case_interpretations (case_id);

-- ── entities ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS case_entities (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    normalized  TEXT,
    confidence  DOUBLE PRECISION,
    source      TEXT,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS entities_case_idx ON case_entities (case_id);

-- ── evidence ──────────────────────────────────────────────────────────────
-- Raw material the user (or a provider) supplied. `content_enc` is sealed under
-- the case subject key, exactly like a document in the erasure pipeline, so a case
-- can be crypto-shredded on request without breaking its chain.
CREATE TABLE IF NOT EXISTS evidence_items (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    workspace     TEXT NOT NULL DEFAULT 'default',
    subject       TEXT,
    kind          TEXT NOT NULL,
    filename      TEXT,
    media_type    TEXT,
    sha256        TEXT NOT NULL,
    bytes         BIGINT,
    content_enc   TEXT,
    text_len      BIGINT,
    trust         TEXT,
    captured_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_case_idx ON evidence_items (case_id);

-- ── facts ─────────────────────────────────────────────────────────────────
-- The fact graph. One row per normalized claim extracted from evidence. Every
-- statement Agent X makes to a user must resolve to rows here, and every row here
-- must resolve to an evidence item through evidence_links — that chain is the
-- whole point of the Evidence Intelligence layer.
CREATE TABLE IF NOT EXISTS evidence_facts (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    predicate    TEXT NOT NULL,
    subject_ref  TEXT,
    value_text   TEXT,
    value_num    DOUBLE PRECISION,
    value_norm   TEXT,
    unit         TEXT,
    confidence   DOUBLE PRECISION NOT NULL,
    method       TEXT,
    status       TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_case_idx ON evidence_facts (case_id, predicate);

CREATE TABLE IF NOT EXISTS evidence_links (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    fact_id      TEXT NOT NULL,
    evidence_id  TEXT NOT NULL,
    locator      TEXT,
    excerpt      TEXT,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS links_fact_idx ON evidence_links (fact_id);
CREATE INDEX IF NOT EXISTS links_case_idx ON evidence_links (case_id);

-- ── contradictions ────────────────────────────────────────────────────────
-- A receipt saying 2,399 and a bank statement saying 2,499 is not a rounding
-- problem to be silently averaged away. It is a fee, a conversion, or the actual
-- dispute. It is recorded and surfaced, never resolved by picking the more
-- convenient number.
CREATE TABLE IF NOT EXISTS contradictions (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    predicate    TEXT NOT NULL,
    fact_a       TEXT NOT NULL,
    fact_b       TEXT NOT NULL,
    severity     TEXT NOT NULL,
    detail       TEXT,
    status       TEXT NOT NULL DEFAULT 'OPEN',
    resolution   TEXT,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS contra_case_idx ON contradictions (case_id);

-- ── policies / rights ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS case_policies (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    policy_id    TEXT NOT NULL,
    title        TEXT,
    authority    TEXT,
    jurisdiction TEXT,
    applies      TEXT NOT NULL,
    because      TEXT,
    citation     TEXT,
    window_days  INT,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS policies_case_idx ON case_policies (case_id);

-- ── remedies ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS remedies (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    title         TEXT,
    eligibility   TEXT NOT NULL,
    confidence    DOUBLE PRECISION,
    expected_value_minor BIGINT,
    because       TEXT,
    blocked_by    TEXT,
    rank          INT,
    created_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS remedies_case_idx ON remedies (case_id);

-- ── plans ─────────────────────────────────────────────────────────────────
-- A plan is a validated graph of steps, not a paragraph of LLM prose. The LLM may
-- propose one; `validated` records whether the deterministic validator accepted it.
CREATE TABLE IF NOT EXISTS plans (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    version       INT NOT NULL DEFAULT 1,
    strategy      TEXT,
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    proposed_by   TEXT,
    validated     TEXT,
    confidence    DOUBLE PRECISION,
    created_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS plans_case_idx ON plans (case_id);

CREATE TABLE IF NOT EXISTS plan_steps (
    id              TEXT PRIMARY KEY,
    plan_id         TEXT NOT NULL,
    case_id         TEXT NOT NULL,
    ordinal         INT NOT NULL,
    step_key        TEXT NOT NULL,
    action          TEXT NOT NULL,
    capability      TEXT,
    title           TEXT,
    params          TEXT,
    prerequisites   TEXT,
    expected        TEXT,
    on_success      TEXT,
    on_failure      TEXT,
    failure_modes   TEXT,
    retry           TEXT,
    wait_days       INT,
    deadline_at     TIMESTAMPTZ,
    risk            TEXT,
    required_level  INT NOT NULL DEFAULT 2,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    attempts        INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS steps_plan_idx ON plan_steps (plan_id, ordinal);
CREATE INDEX IF NOT EXISTS steps_case_idx ON plan_steps (case_id, status);

-- ── authorizations ────────────────────────────────────────────────────────
-- What the user actually agreed to, in the words they were shown. Storing the
-- rendered prompt matters: an authorization is only meaningful if you can prove
-- what was on screen when it was given.
CREATE TABLE IF NOT EXISTS authorizations (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    step_id       TEXT,
    scope         TEXT NOT NULL,
    action        TEXT,
    prompt        TEXT NOT NULL,
    granted       BOOLEAN,
    granted_by    TEXT,
    level         INT,
    constraints   TEXT,
    requested_at  TIMESTAMPTZ NOT NULL,
    decided_at    TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS auth_case_idx ON authorizations (case_id);

-- ── executions ────────────────────────────────────────────────────────────
-- Immutable record of every attempt. There is no UPDATE path that erases a
-- failure: a retry writes a NEW row. "What Agent X tried" and "what happened" are
-- different columns on purpose.
CREATE TABLE IF NOT EXISTS executions (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    step_id       TEXT,
    action        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    provider_mode TEXT NOT NULL,
    request       TEXT,
    state         TEXT NOT NULL,
    result        TEXT,
    external_ref  TEXT,
    evidence_id   TEXT,
    verified      TEXT,
    error         TEXT,
    requested_at  TIMESTAMPTZ NOT NULL,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS exec_case_idx ON executions (case_id, requested_at);

-- ── communications ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS communications (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    direction     TEXT NOT NULL,
    channel       TEXT NOT NULL,
    counterparty  TEXT,
    subject       TEXT,
    body          TEXT,
    external_ref  TEXT,
    execution_id  TEXT,
    sha256        TEXT,
    sent_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS comms_case_idx ON communications (case_id, sent_at);

-- ── deadlines ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deadlines (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    label        TEXT NOT NULL,
    due_at       TIMESTAMPTZ NOT NULL,
    source       TEXT,
    status       TEXT NOT NULL DEFAULT 'PENDING',
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS deadlines_due_idx ON deadlines (status, due_at);

-- ── follow-ups ────────────────────────────────────────────────────────────
-- The scheduler's work queue. Case-aware: a follow-up knows which step it chases
-- and what state the case must still be in for firing it to make sense.
CREATE TABLE IF NOT EXISTS followups (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    step_id       TEXT,
    kind          TEXT NOT NULL,
    due_at        TIMESTAMPTZ NOT NULL,
    require_state TEXT,
    attempt       INT NOT NULL DEFAULT 0,
    max_attempts  INT NOT NULL DEFAULT 3,
    status        TEXT NOT NULL DEFAULT 'SCHEDULED',
    detail        TEXT,
    created_at    TIMESTAMPTZ NOT NULL,
    fired_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS followups_due_idx ON followups (status, due_at);
CREATE INDEX IF NOT EXISTS followups_case_idx ON followups (case_id);

-- ── case_chain ────────────────────────────────────────────────────────────
-- The per-case tamper-evident chain. Same rule as the trust spine's audit_log
-- (content_hash = sha256(prev_hash || canonical(detail)), gap-free seq), in a
-- table that exists on both engines so a case is verifiable wherever it runs.
--
-- Sensitive detail is sealed under the case subject key exactly as
-- core/trust/sealed.py does, so a case can be crypto-shredded on request and the
-- chain still verifies afterwards: the ciphertext was hashed, and it was never
-- touched.
CREATE TABLE IF NOT EXISTS case_chain (
    id             TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL,
    seq            BIGINT NOT NULL,
    step           TEXT NOT NULL,
    actor          TEXT NOT NULL,
    detail         TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    sealed         BOOLEAN NOT NULL DEFAULT FALSE,
    seal_subject   TEXT,
    seal_workspace TEXT,
    ts             TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS case_chain_seq_uq ON case_chain (case_id, seq);

-- ── receipts ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS receipts (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    envelope     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    chain_head   TEXT NOT NULL,
    chain_length INT NOT NULL,
    signed       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_case_idx ON receipts (case_id);

-- ── open questions ────────────────────────────────────────────────────────
-- The minimum-information queue: what Agent X still needs, why it needs it, and
-- which interpretations the answer would separate.
CREATE TABLE IF NOT EXISTS case_questions (
    id            TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    question      TEXT NOT NULL,
    why           TEXT,
    kind          TEXT NOT NULL DEFAULT 'fact',
    options       TEXT,
    separates     TEXT,
    value_bits    DOUBLE PRECISION,
    answer        TEXT,
    status        TEXT NOT NULL DEFAULT 'OPEN',
    created_at    TIMESTAMPTZ NOT NULL,
    answered_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS questions_case_idx ON case_questions (case_id, status);

INSERT INTO schema_migrations (version) VALUES ('005_agentx_cases')
ON CONFLICT (version) DO NOTHING;
