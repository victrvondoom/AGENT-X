-- Agent X — CockroachDB schema
-- One transactional store unifies documents + knowledge graph + vectors + crypto + audit.
-- Every row is scoped to a WORKSPACE (multi-tenant isolation; defaults to 'default').

-- Documents: raw ingested text, one row per source document, owned by a subject/entity.
-- content is stored ENCRYPTED under the (workspace, subject) data key (crypto-shred on erasure).
CREATE TABLE IF NOT EXISTS documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace    STRING NOT NULL DEFAULT 'default',
    subject      STRING NOT NULL,                 -- the data subject / system this doc is about
    title        STRING,
    content_enc  BYTES,                           -- AES-GCM ciphertext of the raw text
    importance   FLOAT8 DEFAULT 0.5,              -- FSFM-style score (drives TTL/retention)
    reviewed_at  TIMESTAMPTZ DEFAULT now(),
    created_at   TIMESTAMPTZ DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    ttl_expire_at TIMESTAMPTZ                      -- row-level TTL: engine deletes the row after this
);
CREATE INDEX IF NOT EXISTS documents_ws_subject_idx ON documents (workspace, subject);

-- Row-level TTL, enforced by the CockroachDB storage engine (a background job reaps expired rows).
-- Expiration is per-row and OPT-IN: rows with a NULL ttl_expire_at never expire, so retention is a
-- policy we set (e.g. on aged/low-importance docs) rather than a blanket clock. Idempotent so both
-- fresh installs and existing clusters converge here.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ttl_expire_at TIMESTAMPTZ;
ALTER TABLE documents SET (ttl_expiration_expression = 'ttl_expire_at', ttl_job_cron = '@hourly');

-- Nodes: knowledge-graph entities extracted from documents (LLM-extracted, coreference-merged).
-- UNIQUE(workspace, name) gives deterministic dedup via INSERT .. ON CONFLICT, per workspace.
CREATE TABLE IF NOT EXISTS nodes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace    STRING NOT NULL DEFAULT 'default',
    name         STRING NOT NULL,
    type         STRING,
    description  STRING,
    embedding    VECTOR(384),                     -- fastembed bge-small-en-v1.5
    weight       FLOAT8 DEFAULT 0.5,              -- reversible demote: 0.05 deep .. 0.5 neutral
    doc_ids      UUID[] DEFAULT ARRAY[]::UUID[],  -- provenance: source documents
    subjects     STRING[] DEFAULT ARRAY[]::STRING[], -- subjects whose docs produced this node
    created_at   TIMESTAMPTZ DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    UNIQUE (workspace, name)
);
CREATE VECTOR INDEX IF NOT EXISTS nodes_embedding_idx ON nodes (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS nodes_ws_idx ON nodes (workspace);

-- Edges: relationships between nodes (the graph). doc_ids = provenance.
CREATE TABLE IF NOT EXISTS edges (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace     STRING NOT NULL DEFAULT 'default',
    source_id     UUID NOT NULL,
    target_id     UUID NOT NULL,
    relationship  STRING,
    description   STRING,
    doc_ids       UUID[] DEFAULT ARRAY[]::UUID[],
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS edges_source_idx ON edges (source_id);
CREATE INDEX IF NOT EXISTS edges_target_idx ON edges (target_id);
CREATE INDEX IF NOT EXISTS edges_ws_idx ON edges (workspace);

-- Per-(workspace, subject) data-encryption keys. Erasure DESTROYS wrapped_dek => residual
-- ciphertext (MVCC history, backups, S3) becomes cryptographically unrecoverable.
CREATE TABLE IF NOT EXISTS subject_keys (
    workspace    STRING NOT NULL DEFAULT 'default',
    subject      STRING NOT NULL,
    wrapped_dek  BYTES,                            -- DEK wrapped (AES-GCM) by the root key
    created_at   TIMESTAMPTZ DEFAULT now(),
    destroyed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace, subject)
);

-- Erasure audit log + certificate registry (mirrored to object-locked S3).
CREATE TABLE IF NOT EXISTS erasure_events (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace         STRING NOT NULL DEFAULT 'default',
    subject           STRING,
    subject_salt      BYTES,                       -- random per-event salt for the cert's subject hash (never leaves in the portable cert)
    t_before          DECIMAL,                     -- cluster_logical_timestamp() pre-delete (AOST anchor)
    docs_removed      INT DEFAULT 0,
    nodes_removed     INT DEFAULT 0,
    edges_removed     INT DEFAULT 0,
    nodes_invalidated INT DEFAULT 0,
    cert_sha256       STRING,
    cert_s3_key       STRING,
    cert_json         JSONB,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- Migration for clusters created before subject_salt existed. Placed AFTER the CREATE
-- TABLE above: ADD COLUMN IF NOT EXISTS guards the COLUMN, not the TABLE, so running it
-- before the table exists fails on a fresh install.
ALTER TABLE erasure_events ADD COLUMN IF NOT EXISTS subject_salt BYTES;

-- Workspaces: named, isolated knowledge bases (multi-tenancy).
CREATE TABLE IF NOT EXISTS workspaces (
    id         STRING PRIMARY KEY,
    name       STRING,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Memory timeline: ingest / forget / demote / review events for the UI.
CREATE TABLE IF NOT EXISTS timeline (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace  STRING NOT NULL DEFAULT 'default',
    kind       STRING,
    subject    STRING,
    detail     STRING,
    created_at TIMESTAMPTZ DEFAULT now()
);
