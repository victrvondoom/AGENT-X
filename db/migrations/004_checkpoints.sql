-- ═══════════════════════════════════════════════════════════════════════════
-- 004_checkpoints.sql — transparency checkpoints
--
-- Closes the limitation documented in Phase 5. A certificate carries the key its
-- own signature is checked against, so it can never prove it is not a forgery on
-- its own terms. A published Merkle root can: a genuine certificate proves it was
-- included in a checkpoint published BEFORE the dispute, and a forgery made later
-- cannot be, because inserting it would change a root that is already public.
--
-- Forging stops being "generate a keypair" and becomes "alter the past".
--
-- The value of a checkpoint comes entirely from being published somewhere we do
-- not control -- a git commit, a status page, a timestamping service. Stored here
-- so it can be exported; storing it ONLY here would prove nothing, since we could
-- rewrite this table too.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS checkpoints (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merkle_root TEXT NOT NULL,
    leaf_count  INT  NOT NULL,
    note        TEXT,
    published   TEXT,                    -- where it was published, once it is
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS checkpoints_time_idx ON checkpoints (created_at DESC);

INSERT INTO schema_migrations (version) VALUES ('004_checkpoints')
ON CONFLICT (version) DO NOTHING;
