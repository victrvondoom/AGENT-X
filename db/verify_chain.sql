-- ═══════════════════════════════════════════════════════════════════════════
-- INDEPENDENT AUDIT-CHAIN VERIFICATION
--
-- Run this directly against the database. It does not call the application, does
-- not use any of our functions, and does not trust the stored hashes: it RECOMPUTES
-- each row's hash from the row's own content and compares.
--
-- That distinction matters. A query that merely reads content_hash and checks it
-- links to prev_hash proves only that the columns are consistent with each other --
-- which is exactly what an attacker who edited a row would leave behind. This
-- re-derives sha256(prev_hash || canonical_detail) and catches it.
--
--   psql  "$DATABASE_URL" -v job="'<uuid>'" -f db/verify_chain.sql
--   cockroach sql --url "$DATABASE_URL" --set job="'<uuid>'" -f db/verify_chain.sql
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Re-derive every hash and compare to what is stored ─────────────────
WITH RECURSIVE
walk AS (
    -- the first entry must chain from the all-zero genesis hash
    SELECT a.seq,
           a.step,
           a.actor,
           a.prev_hash,
           a.content_hash,
           encode(digest(repeat('0', 64) || a.detail_canonical, 'sha256'), 'hex') AS recomputed
    FROM audit_log a
    WHERE a.job_id = :job AND a.seq = 0

    UNION ALL

    SELECT n.seq,
           n.step,
           n.actor,
           n.prev_hash,
           n.content_hash,
           encode(digest(w.content_hash || n.detail_canonical, 'sha256'), 'hex')
    FROM audit_log n
    JOIN walk w ON n.seq = w.seq + 1
    WHERE n.job_id = :job
)
SELECT seq,
       step,
       actor,
       CASE WHEN recomputed = content_hash THEN 'OK' ELSE 'TAMPERED' END AS hash_check,
       left(content_hash, 16) AS stored,
       left(recomputed,    16) AS recomputed
FROM walk
ORDER BY seq;

-- ── 2. One-line verdict ───────────────────────────────────────────────────
-- Checks three things a tamperer has to defeat simultaneously:
--   * every recomputed hash matches its stored hash   (no row was edited)
--   * every prev_hash equals the previous content_hash (the chain is linked)
--   * seq runs 0..n-1 with no gaps                     (no row was deleted)
WITH RECURSIVE
walk AS (
    SELECT a.seq, a.detail, a.prev_hash, a.content_hash,
           encode(digest(repeat('0', 64) || a.detail_canonical, 'sha256'), 'hex') AS recomputed
    FROM audit_log a WHERE a.job_id = :job AND a.seq = 0
    UNION ALL
    SELECT n.seq, n.detail, n.prev_hash, n.content_hash,
           encode(digest(w.content_hash || n.detail_canonical, 'sha256'), 'hex')
    FROM audit_log n JOIN walk w ON n.seq = w.seq + 1 WHERE n.job_id = :job
),
linked AS (
    SELECT w.*,
           lag(w.content_hash) OVER (ORDER BY w.seq) AS expected_prev
    FROM walk w
)
SELECT
    count(*)                                                   AS rows_walked,
    (SELECT count(*) FROM audit_log WHERE job_id = :job)        AS rows_in_table,
    count(*) FILTER (WHERE recomputed <> content_hash)          AS tampered_rows,
    count(*) FILTER (WHERE seq > 0 AND prev_hash <> expected_prev) AS broken_links,
    max(seq) + 1 - count(*)                                     AS sequence_gaps,
    CASE
      WHEN count(*) <> (SELECT count(*) FROM audit_log WHERE job_id = :job)
        THEN 'FAIL - the walk stopped early: a row is missing from the middle'
      WHEN count(*) FILTER (WHERE recomputed <> content_hash) > 0
        THEN 'FAIL - a row was edited: its content no longer hashes to its stored hash'
      WHEN count(*) FILTER (WHERE seq > 0 AND prev_hash <> expected_prev) > 0
        THEN 'FAIL - the chain was re-linked'
      WHEN max(seq) + 1 <> count(*)
        THEN 'FAIL - sequence gap: a row was deleted'
      ELSE 'PASS - chain intact, every hash re-derived independently'
    END                                                        AS verdict
FROM linked;

-- ── 3. Compare against the certificate ────────────────────────────────────
-- The head hash and the LENGTH together. A chain truncated from the tail is still
-- internally valid, so length is what catches it.
SELECT c.job_id,
       c.audit_head                                        AS certificate_head,
       (SELECT content_hash FROM audit_log
         WHERE job_id = c.job_id ORDER BY seq DESC LIMIT 1) AS live_head,
       (c.cert_json ->> 'chain_length')::int               AS certificate_length,
       (SELECT count(*) FROM audit_log WHERE job_id = c.job_id) AS live_length,
       CASE WHEN c.audit_head = (SELECT content_hash FROM audit_log
                                  WHERE job_id = c.job_id ORDER BY seq DESC LIMIT 1)
             AND (c.cert_json ->> 'chain_length')::int
                 = (SELECT count(*) FROM audit_log WHERE job_id = c.job_id)
            THEN 'PASS - the database still matches the signed certificate'
            ELSE 'FAIL - the chain no longer matches what was attested'
       END AS verdict
FROM certificates c
WHERE c.job_id = :job;
