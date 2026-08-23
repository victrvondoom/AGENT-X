-- ═══════════════════════════════════════════════════════════════════════════
-- 009_research.sql — what Agent X read, and whether it checked out
--
-- `policy.py` decides entitlement from declarative conditions. It is narrow on
-- purpose, and that narrowness left a real gap: when a case needed the
-- PROCEDURAL half of an answer — which ombudsman, within how many days, what the
-- published compensation band actually is — Agent X had nowhere to look and said
-- nothing.
--
-- This table is where looking is recorded. Each row is one passage of regulatory
-- guidance retrieved for one case, together with the verdict of checking a claim
-- against it. Retrieval is deterministic (BM25 over a corpus checked into the
-- repository), so a row here is reproducible: the same case re-run retrieves the
-- same passages, which is the only reason research is allowed onto the chain at
-- all.
--
-- WHAT A ROW HERE MAY AND MAY NOT DO
--
-- It may inform the user and supply a citation for a letter. It may NOT
-- establish an entitlement, set an amount, or unlock an action. Those remain
-- `policy.py`'s and the governor's, evaluated from facts. A retrieved passage
-- that appears to grant a right the policy corpus does not model is a passage
-- Agent X quotes and attributes — never one it acts on. The separation is why a
-- corpus of authored guidance can be useful here without becoming a way to
-- smuggle an unverified rule into a consequential decision.
--
-- `verdict` is four-valued, never boolean, matching agentx/knowledge/verify.py:
--   verified     the passage supports the claim
--   partial      on topic, but does not establish it
--   unsupported  nothing retrieved supports it
--   conflicting  the passage states something incompatible (usually a figure)
--
-- "could not confirm" and "is contradicted" are different facts and a user is
-- entitled to be told which. Only `verified` may be stated to a counterparty as
-- established; `letters.py` enforces that.
--
-- NO PERSONAL DATA. Like `case_outcomes` (007), the columns are the STRUCTURE of
-- a research step: which passage, which sector, what verdict, what score. The
-- claim text is the agent's own assertion about regulation, not the user's
-- narrative. A shredded case's research rows stay readable and say nothing about
-- the person — the citation "RBI requires shadow reversal within 10 working
-- days" is not theirs, it is the regulator's.
--
-- Portable subset (see 005): runs on CockroachDB and SQLite alike.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS case_research (
    id             TEXT PRIMARY KEY,
    workspace      TEXT NOT NULL DEFAULT 'default',
    case_id        TEXT NOT NULL,
    -- The retrieved passage. `passage_id` is stable across runs (doc id + ordinal),
    -- so a citation can be resolved back to the exact text that supported it.
    passage_id     TEXT NOT NULL,
    sector         TEXT,
    title          TEXT,
    authority      TEXT,
    citation       TEXT,
    -- BM25 score and the share of the query's distinctive mass this passage
    -- captured. Kept so a weak citation can be argued with rather than trusted.
    score          DOUBLE PRECISION,
    coverage       DOUBLE PRECISION,
    -- Retrieval rank, stored rather than re-derived. `created_at` is stamped at
    -- second resolution, so every row of one research step shares a timestamp and
    -- ordering by it silently fell back to the id — presenting the third-best
    -- passage first. Relevance order is part of what the record means.
    rank           INT NOT NULL DEFAULT 0,
    -- The claim checked against this passage, and the result. NULL claim means
    -- the passage was retrieved as background and no claim was tested against it.
    claim          TEXT,
    verdict        TEXT,
    because        TEXT,
    -- Stamped by the application (`ids.now()`), not by a database default: the
    -- same reason 005 gives for minting ids in Python. A DEFAULT now() would also
    -- not parse on SQLite, which has no such function.
    created_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS case_research_by_case
    ON case_research (workspace, case_id, created_at);

-- `rank` was added after this migration had already been applied to working
-- databases, and `CREATE TABLE IF NOT EXISTS` is a no-op against an existing
-- table — so the column above never appears on one. This ALTER is what actually
-- adds it there. On a fresh database the column already exists and the statement
-- fails as a duplicate, which `store._apply` treats as the no-op it is (see the
-- note there, and migrations 007/008 which use the same pattern).
--
-- Caught by running the app against a real database rather than by the suite:
-- every test opens a fresh file via `reset_for_tests`, so no test could ever
-- observe the upgrade path that every existing deployment takes.
ALTER TABLE case_research ADD COLUMN rank INT DEFAULT 0;

INSERT INTO schema_migrations (version) VALUES ('009_research')
ON CONFLICT (version) DO NOTHING;
