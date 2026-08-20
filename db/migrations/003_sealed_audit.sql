-- ═══════════════════════════════════════════════════════════════════════════
-- 003_sealed_audit.sql — crypto-shreddable audit detail
--
-- THE CONFLICT THIS RESOLVES
--
-- An audit chain must be immutable, or it proves nothing. The right to erasure
-- requires personal data to be destroyed on request. The chain CONTAINS personal
-- data -- a reviewer's name, a corrected bank account number, a subject's own
-- values -- and deleting a hash-chained row destroys every hash after it.
--
-- So the two obligations appear to be mutually exclusive, and most systems pick
-- one: an audit log you cannot erase from, or an "audit log" you can quietly edit.
--
-- The resolution is the mechanism the erasure pipeline already uses for documents.
-- Sensitive detail is SEALED under the subject's data-encryption key, and the
-- chain hashes the CIPHERTEXT. Erasure destroys the key. Afterwards:
--
--   * every hash still matches -- the ciphertext was never touched, so the chain
--     remains verifiable forever and still proves the event happened, when, and
--     in what order;
--   * the content is cryptographically unrecoverable, because the key is gone.
--
-- You keep the proof and lose the personal data. That is precisely what GDPR
-- Art. 17 asks for and what an immutable ledger normally cannot give.
--
-- This is where the two pipelines actually meet: the document pipeline's audit
-- trail is made erasable by the erasure pipeline's crypto-shred.
-- ═══════════════════════════════════════════════════════════════════════════

-- Sealed rows carry ciphertext in detail_canonical (base64) and record which
-- subject's key opens them. `sealed` is explicit rather than inferred from a NULL,
-- so a reader can distinguish "sealed and shredded" from "never sealed".
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS sealed          BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS seal_subject    TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS seal_workspace  TEXT;

-- Which document jobs an erasure reached into. A subject's evidence may sit in a
-- compliance job that must itself be retained, so the erasure needs to record what
-- it redacted rather than silently deleting a legal record.
ALTER TABLE fields ADD COLUMN IF NOT EXISTS redacted_at     TIMESTAMPTZ;
ALTER TABLE fields ADD COLUMN IF NOT EXISTS redaction_job   UUID;

CREATE INDEX IF NOT EXISTS jobs_subject_idx ON jobs (workspace, subject);
CREATE INDEX IF NOT EXISTS audit_seal_idx   ON audit_log (seal_workspace, seal_subject);

INSERT INTO schema_migrations (version) VALUES ('003_sealed_audit')
ON CONFLICT (version) DO NOTHING;
