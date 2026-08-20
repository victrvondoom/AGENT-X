-- ═══════════════════════════════════════════════════════════════════════════
-- 002_canonical_detail.sql
--
-- Makes independent SQL verification actually possible.
--
-- The chain hashes sha256(prev_hash || canonical(detail)), where canonical() is
-- Python's compact sorted-key JSON. `detail` is JSONB, and a database renders JSONB
-- its own way -- Postgres inserts a space after ':' and ',', CockroachDB may order
-- keys differently again. So `digest(prev_hash || detail::text, 'sha256')` in SQL
-- can NEVER reproduce the stored hash, and the "verify it yourself in SQL" claim
-- was quietly false.
--
-- Storing the exact bytes that were hashed fixes it. `detail` stays JSONB so the
-- audit trail is still queryable; `detail_canonical` is the hashed pre-image, so a
-- judge can re-derive every hash with nothing but digest() and this column.
--
-- Keeping both is not redundancy -- it is the difference between a trail you can
-- read and a trail you can prove.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS detail_canonical TEXT;

INSERT INTO schema_migrations (version) VALUES ('002_canonical_detail')
ON CONFLICT (version) DO NOTHING;
