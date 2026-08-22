-- ═══════════════════════════════════════════════════════════════════════════
-- 007_outcomes.sql — what actually worked, remembered across cases
--
-- Every closed case writes one row here, and every new case against the same
-- counterparty and problem type reads them back before it plans. That is the
-- closed loop the resolution engine was missing: without it, Agent X negotiates
-- with Kartly for the hundredth time exactly the way it did the first, having
-- learned nothing from ninety-nine prior refusals it personally handled.
--
-- THIS TABLE CONTAINS NO PERSONAL DATA, BY CONSTRUCTION
--
-- Not "PII is stripped later" — there is nowhere to put it. The columns are the
-- STRUCTURE of a resolution, never its content: which company, which problem
-- class, which remedy, how many chases it took, whether escalation was needed,
-- how long it ran, and what fraction of the claim came back. No amounts in
-- currency, no references, no narrative, no user.
--
-- That is a deliberate design decision with a specific consequence, and it is
-- the most interesting property of this table: **the learning survives erasure.**
-- When a user exercises their right to be forgotten, `case.forget()` destroys
-- the case's key and its content becomes unrecoverable — but the knowledge that
-- "Kartly settles duplicate-charge claims only after escalation" was never
-- personal data, was never sealed under that key, and is still true. A system
-- that stored outcomes as summaries of case CONTENT would have to choose between
-- honouring the erasure and keeping what it learned. Storing structure instead
-- means there is no conflict to resolve.
--
-- `case_id` is kept only as a provenance pointer so a claim like "based on 3
-- prior cases" can be audited. It is a foreign reference by convention, not
-- constraint: a shredded case's row stays, and points at a case whose contents
-- no longer exist — which is exactly right.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS case_outcomes (
    id                TEXT PRIMARY KEY,
    workspace         TEXT NOT NULL DEFAULT 'default',
    case_id           TEXT NOT NULL,
    counterparty      TEXT,                 -- normalised company name, or NULL
    problem_type      TEXT,
    domain            TEXT,
    strategy          TEXT,                 -- the remedy actually pursued
    outcome           TEXT NOT NULL,        -- resolved | unresolved | withdrawn
    -- recovered / claimed, as a 0..1 ratio. A RATIO, not an amount: it carries
    -- the useful signal (did they pay in full, in part, or not at all) with none
    -- of the personal specificity an amount would.
    recovery_ratio    DOUBLE PRECISION,
    chases_needed     INT NOT NULL DEFAULT 0,
    escalated         BOOLEAN NOT NULL DEFAULT FALSE,
    escalated_to      TEXT,
    days_to_close     DOUBLE PRECISION,
    cited_rights      TEXT,                 -- JSON list of policy ids that were cited
    provider_mode     TEXT,                 -- sandbox | live — never conflated
    created_at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS outcomes_lookup_idx
    ON case_outcomes (workspace, counterparty, problem_type);
CREATE INDEX IF NOT EXISTS outcomes_problem_idx
    ON case_outcomes (workspace, problem_type);

-- The prior a plan was shaped by, carried on the plan itself so a user reading a
-- case months later can see what experience said at the time — including when it
-- said "not enough to act on". CockroachDB accepts IF NOT EXISTS here; SQLite
-- does not support it on ADD COLUMN, so `store._apply` treats a duplicate-column
-- error on an ALTER as the no-op it is (see the comment there).
ALTER TABLE plans ADD COLUMN prior TEXT;

INSERT INTO schema_migrations (version) VALUES ('007_outcomes')
ON CONFLICT (version) DO NOTHING;
