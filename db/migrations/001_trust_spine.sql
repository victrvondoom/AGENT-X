-- ═══════════════════════════════════════════════════════════════════════════
-- 001_trust_spine.sql — the shared trust core
--
-- This migration belongs to NEITHER pipeline. It is the spine both of them hang
-- off: the erasure pipeline and the document pipeline write to the same audit
-- chain, are gated by the same human-approval primitive, and are attested by the
-- same certificate table. Adding a third pipeline later means adding rows here,
-- not a second trust system.
--
-- Portable across PostgreSQL 16 and CockroachDB — same wire protocol, and every
-- type below (BYTEA, JSONB, TIMESTAMPTZ, gen_random_uuid) exists on both.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── jobs ──────────────────────────────────────────────────────────────────
-- One row per run of any pipeline. `kind` is what makes this one product rather
-- than two: 'document' and 'erasure' are values, not separate tables.
CREATE TABLE IF NOT EXISTS jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        TEXT NOT NULL DEFAULT 'document',
    doc_type    TEXT,
    subject     TEXT,                     -- erasure pipeline: who is being forgotten
    workspace   TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL DEFAULT 'EXTRACTING',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_kind_ck   CHECK (kind IN ('document','erasure')),
    CONSTRAINT jobs_status_ck CHECK (status IN
        ('EXTRACTING','NEEDS_REVIEW','APPROVED','SIGNED','VERIFIED','FAILED','REFUSED'))
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status);
CREATE INDEX IF NOT EXISTS jobs_ws_idx     ON jobs (workspace, created_at);

-- ── fields ────────────────────────────────────────────────────────────────
-- One row per extracted value, carrying the routing decision that sent it to a
-- machine or a human.
--
-- decision_reason exists because Nutrient's own docs are explicit that an ABSENT
-- confidence "means no score was available. It doesn't mean low confidence."
-- Collapsing that into 0.0 would flood the review queue; defaulting it high would
-- silently auto-accept unscored fields, which in a compliance product is the worse
-- failure. It is recorded as its own reason so the audit trail can distinguish
-- "the model was unsure" from "the model said nothing".
CREATE TABLE IF NOT EXISTS fields (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    value           TEXT,
    confidence      DOUBLE PRECISION,     -- NULL = no signal, NOT low confidence
    recognition     DOUBLE PRECISION,     -- OCR legibility, gates independently
    source_bbox     JSONB,
    decision        TEXT NOT NULL DEFAULT 'HUMAN',
    decision_reason TEXT,
    original_value  TEXT,                 -- what the machine said, before a human edit
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fields_decision_ck CHECK (decision IN ('AUTO','HUMAN','PENDING'))
);
CREATE INDEX IF NOT EXISTS fields_job_idx ON fields (job_id);

-- ── audit_log ─────────────────────────────────────────────────────────────
-- The trust primitive. Append-only and hash-chained: each row's prev_hash is the
-- previous row's content_hash, so altering ANY historical row breaks every hash
-- after it. A regulator does not have to trust the application to believe this —
-- they can re-walk the chain themselves in SQL.
--
-- seq is per-job and gap-free, which is what makes a DELETED row detectable.
-- Hash-chaining alone cannot catch a truncation from the tail; a contiguous
-- sequence can.
CREATE TABLE IF NOT EXISTS audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seq          BIGINT NOT NULL,
    step         TEXT NOT NULL,
    actor        TEXT NOT NULL,
    detail       JSONB NOT NULL,
    prev_hash    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT audit_actor_ck CHECK (actor IN ('AGENT','HUMAN','SYSTEM')),
    CONSTRAINT audit_seq_uq   UNIQUE (job_id, seq)
);
CREATE INDEX IF NOT EXISTS audit_job_seq_idx ON audit_log (job_id, seq);

-- ── certificates ──────────────────────────────────────────────────────────
-- The portable attestation. Binds the audit head to the signed artefact, so
-- verifying the certificate transitively verifies the whole run.
CREATE TABLE IF NOT EXISTS certificates (
    job_id      UUID PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    cert_json   JSONB NOT NULL,
    sha256      TEXT NOT NULL,
    signature   TEXT,
    public_key  TEXT,
    audit_head  TEXT NOT NULL,            -- content_hash of the last audit row
    doc_sha256  TEXT,                     -- hash of the signed document
    signed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES ('001_trust_spine')
ON CONFLICT (version) DO NOTHING;
