# TrustDoc — a verifiable compliance-document agent

**A messy regulated document goes in. A correct, signed, compliant one comes out —
and a skeptical engineer can verify it without trusting a single line of our code.**

TrustDoc is one half of Agent X. The other half provably *forgets* data. Both are the
same machine: an action on regulated data is gated by a human when it is uncertain,
recorded on a tamper-evident chain, attested by a signed certificate, and checkable by
someone who assumes we are lying.

---

## Where Nutrient DWS does the real work, and why

> **Nutrient DWS `/extract` does the deterministic document work — it maps a scanned
> invoice to a JSON Schema and returns every field with a confidence signal and a
> citation back to the exact page region. That per-field confidence is what the entire
> product is built on: without a trustworthy uncertainty signal there is nothing to
> route on, and "which fields need a human" collapses into guesswork.**

We do not ask an LLM to read the document. An LLM that misreads a bank account number
does so with total fluency and no signal that anything went wrong. DWS returns
`confidence`, `confidenceComponents`, `recognitionScore` and `source_bboxes` per field
— structured uncertainty, which is the raw material for a confidence gate.

DWS also provides `/sign` for the document signature and the Viewer for human review of
the source region.

---

## The four patterns

| # | Pattern | Where |
|---|---|---|
| 1 | **Confidence-gate router** | `core/trust/gate.py` |
| 2 | **Human-in-the-loop approval** | `pipelines/document/review.py` |
| 3 | **Self-verifying loop** | `pipelines/document/finalize.py` |
| 4 | **Signed certificate + independent verification** | `core/trust/certificate.py`, `db/verify_chain.sql`, `templates/verify_offline.html` |

### 1 — The confidence gate is not a single threshold

Nutrient's own documentation says two things that break the obvious implementation:

- confidence is *"relative and uncalibrated; it isn't a probability or percentage"*
- *"An absent confidence value means that no score was available. It doesn't mean low confidence."*

So comparing everything to a flat `0.85` implies a precision the number does not have,
and treating `absent` as `0.0` floods the queue while treating it as `1.0` silently
auto-accepts unscored fields — the worse failure in a compliance product.

What we do instead:

- **Per-field-type thresholds.** `account_number` 0.97, `total` 0.95, `description` 0.70.
  Money and identifiers cause real harm when wrong; descriptions do not.
- **Absent is its own branch**, routed to a human with `decision_reason='no_confidence_signal'`,
  so the audit trail distinguishes *"the model was unsure"* from *"the model said nothing"*.
- **OCR legibility gates independently.** A field can be confidently read off an
  illegible region — in our demo `account_number` arrives at **0.99 confidence over
  0.41 legibility** and is held. A flat gate would have auto-accepted a bank account
  number the model could barely see.
- **The policy is recorded in the audit entry**, so every routing decision is
  re-derivable later without our binary.

### 2 — The human gate is in SQL, not in the application

`NEEDS_REVIEW → APPROVED` is a single guarded UPDATE:

```sql
UPDATE jobs SET status = 'APPROVED'
WHERE id = %s AND status = 'NEEDS_REVIEW'
  AND NOT EXISTS (SELECT 1 FROM fields
                  WHERE job_id = %s AND decision = 'HUMAN' AND reviewed_at IS NULL);
```

Calling the internal resume function directly does not advance the job. `decision` and
`reviewed_at` are kept separate on purpose: the first means *routed* to a human, the
second means one *ruled*. Collapse them and "a human approved this" becomes
indistinguishable from "a machine sent this to a human".

### 3 — The self-verifying loop re-reads the artefact

After generating and signing, the agent parses the **produced file** and compares every
field to the approved value. It does not compare a dict to itself — flipping a byte
inside the PDF changes the read-back, and there is a test that asserts exactly that.
A mismatch sets `FAILED` and names the field. Nothing ships silently.

### 4 — Independent verification, three ways

1. **Offline verifier** — `templates/verify_offline.html`. Save it, disconnect your
   network, open it from disk. It recomputes the SHA-256 and verifies the ECDSA P-256
   signature in WebCrypto. There is no server to trust.
2. **Raw SQL** — see below. It *recomputes* every hash rather than reading it back.
3. **Key comparison** — the certificate carries its public key; compare it to one
   published separately.

---

## Run the audit check yourself

Nothing below touches our application code.

```bash
psql "$DATABASE_URL" -v job="'<job-uuid>'" -f db/verify_chain.sql
```

The core of it — re-deriving the chain from scratch:

```sql
WITH RECURSIVE walk AS (
    SELECT a.seq, a.prev_hash, a.content_hash,
           encode(digest(repeat('0',64) || a.detail_canonical, 'sha256'),'hex') AS recomputed
    FROM audit_log a WHERE a.job_id = :job AND a.seq = 0
  UNION ALL
    SELECT n.seq, n.prev_hash, n.content_hash,
           encode(digest(w.content_hash || n.detail_canonical, 'sha256'),'hex')
    FROM audit_log n JOIN walk w ON n.seq = w.seq + 1 WHERE n.job_id = :job
)
SELECT count(*)                                          AS rows_walked,
       count(*) FILTER (WHERE recomputed <> content_hash) AS tampered_rows,
       CASE WHEN count(*) FILTER (WHERE recomputed <> content_hash) = 0
            THEN 'PASS - every hash re-derived independently'
            ELSE 'FAIL - a row was edited' END            AS verdict
FROM walk;
```

`detail_canonical` holds the exact bytes that were hashed. Without it, SQL would have to
guess how Python serialised the JSON — which it cannot — and the "verify it yourself"
claim would be false. That bug was real and is fixed.

---

## Setup

```bash
docker compose up -d                       # Postgres 16
python -m venv .venv && .venv/bin/pip install -r requirements.txt

cp .env.example .env                       # then set:
#   DATABASE_URL=postgresql://trustdoc:trustdoc@127.0.0.1:5432/trustdoc
#   DWS_API_KEY=<your Nutrient DWS key>
#   AGENT_X_SIGNING_KEY=<python -c "from pipelines.document import sign; print(sign.generate_signing_key())">

python scripts/init_trust.py               # migrate + prove the chain detects tampering
uvicorn app.main:app --reload              # then open http://127.0.0.1:8000/trustdoc
```

Use `127.0.0.1`, not `localhost`: on Windows, `localhost` resolves to IPv6 `::1` first
and psycopg waits on it indefinitely.

Phase demos, each self-checking:

```
python scripts/init_trust.py     # audit chain vs 4 tampering attacks   10/10
python scripts/demo_phase2.py    # confidence gate + routing             PASS
python scripts/demo_phase3.py    # human review, resume guard             9/9
python scripts/demo_phase4.py    # sign + self-verify, clean and caught   7/7
python scripts/demo_phase5.py    # certificate + 4 forgery attacks        8/8
```

---

## Honest limitations

**A certificate cannot vouch for itself.** It carries the public key its signature is
checked against, so anyone can edit a value, sign with their own key, and embed their
own key — hash and signature both pass. This is a forged document with forged
letterhead, and nothing self-contained detects it. Two things do, and a verifier must
do at least one: **pin the key** against a separately published one, or **check the
chain binding**, since a forgery is not in the database. The offline verifier reports
the chain check as *not performed* rather than implying it passed.

**A truncated tail leaves a valid chain.** Hash-chaining alone cannot see rows deleted
from the end — the survivors still link. The certificate therefore pins `chain_length`
alongside `chain_head`, so truncation is caught at certificate verification. Deletions
from the *middle* are caught immediately by the gap-free `seq`.

**Append-only is enforced by convention plus detection, not by permissions.** A
database superuser can still `UPDATE audit_log`. What they cannot do is make the result
verify. Revoking `UPDATE`/`DELETE` from the application role is a deployment step we
document but do not perform for you.

**The confidence numbers are a policy, not a measurement.** The per-field thresholds
were chosen by risk, not tuned on a labelled corpus. They ship inside the certificate so
you can disagree with them from the evidence rather than from the source code.

**Not yet exercised against a live DWS account.** The `/extract` and `/sign` clients are
written against the documented endpoints and fail loudly (`DWSUnavailable`) without a
key rather than returning synthetic data. Runs fed by a recorded extraction are marked
`engine='recorded-fixture'` **on the audit chain**, so a demo can never be mistaken for
a live run. Whether `/sign` offers ECDSA P-256 specifically is not stated in Nutrient's
public documentation and must be confirmed against a real account — if it is RSA-only,
the document signature and the certificate signature are different algorithms and this
document will say so.
