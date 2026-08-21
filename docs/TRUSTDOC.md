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

## The six patterns

| # | Pattern | Where |
|---|---|---|
| 1 | **Confidence-gate router** | `core/trust/gate.py` |
| 2 | **Human-in-the-loop approval** | `pipelines/document/review.py` |
| 3 | **Self-verifying loop** | `pipelines/document/finalize.py` |
| 4 | **Signed certificate + independent verification** | `core/trust/certificate.py`, `db/verify_chain.sql`, `templates/verify_offline.html` |
| 5 | **Crypto-shreddable audit detail** | `core/trust/sealed.py` |
| 6 | **Transparency checkpoints** | `core/trust/merkle.py` |

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

### 5 — Crypto-shreddable audit detail: where the two pipelines collide

An audit chain must be immutable or it proves nothing. Erasure must destroy personal
data. The chain **contains** personal data — a reviewer's name, a corrected account
number. Deleting a hash-chained row destroys every hash after it. Most systems quietly
pick one obligation over the other.

The resolution: seal the sensitive half of each entry under the **subject's own key**
and hash the ciphertext. Erasure destroys the key. Afterwards every hash still matches,
because the ciphertext was never touched — the chain still proves what happened and
when — while the content is cryptographically unrecoverable.

```
chain STILL verifies: 7 rows; all 6 pre-erasure hashes unchanged
detail now: <crypto-shredded: the key for this subject was destroyed>
but still legible: field=account_number  action=CORRECT
```

Only *values* are sealed — step, actor, and timestamp stay clear, so a regulator can
still read "a human corrected a field on this date" after a shred. A redaction reaches
a subject's document jobs but **retains the job itself**: the compliance record is
itself evidence the request was handled lawfully, so deleting it would destroy the
proof of compliance. Endpoints: `GET /api/doc/jobs/{id}/audit/sealed`,
`POST /api/doc/redact`.

### 6 — Transparency checkpoints: closing the self-vouching gap

Pattern 4's limitation is real and cannot be closed by cryptography alone: a
certificate carries the public key its own signature is checked against, so a forger
can edit a value, sign with their own key, and embed that key. Hash and signature both
pass — it is a forged document with forged letterhead.

What closes it is **time**. Periodically fold every job's chain into a Merkle root and
publish it somewhere outside our control — a git commit, a status page. A genuine
certificate proves it was included in a checkpoint published *before* any dispute; a
forgery minted afterward cannot be, because inserting it would move a root that is
already public. Forging stops being "generate a keypair" and becomes "alter the past".

Endpoints: `POST /api/doc/checkpoint`, `GET /api/doc/checkpoints`,
`GET /api/doc/jobs/{id}/inclusion`. Rendered live at **`/spine`** — the trust chain in
3D, built from real checkpoint and chain data, not a mock.

---

## API reference

| Method | Path | What |
|---|---|---|
| POST | `/api/doc/upload` | extract a document (needs `DWS_API_KEY`) |
| POST | `/api/doc/demo-job` | seed a job from a recorded extraction, no key needed |
| GET | `/api/doc/jobs/{id}` | job status, fields, pending review |
| POST | `/api/doc/jobs/{id}/review` | rule on one field |
| POST | `/api/doc/jobs/{id}/finalize` | generate, sign, self-verify, certify |
| GET | `/api/doc/jobs/{id}/certificate` | the portable certificate |
| POST | `/api/doc/verify` | verify any certificate |
| GET | `/api/doc/jobs/{id}/audit` | raw chain + verification |
| GET | `/api/doc/jobs/{id}/audit/sealed` | decrypted chain view |
| POST | `/api/doc/redact` | redact a subject's field values |
| POST | `/api/doc/checkpoint` | publish a Merkle root |
| GET | `/api/doc/checkpoints` | list published checkpoints |
| GET | `/api/doc/jobs/{id}/inclusion` | prove/disprove checkpoint inclusion |
| GET | `/api/doc/spine-data` | live chain summary, feeds `/spine` |

Pages: `/trustdoc` (5-screen flow) · `/spine` (trust chain in 3D) ·
`/verify-offline` (offline certificate verifier) · `/cascade` (erasure cascade in 3D)

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
python scripts/demo_phase7.py    # crypto-shreddable audit detail         8/8
python scripts/demo_phase8.py    # transparency checkpoints               9/9
pytest tests/ -v                 # 29 unit tests, DB-dependent ones skip cleanly
```

### Scheduled checkpoints

A checkpoint only means something if it is published on a *regular cadence before
disputes happen* — one minted on demand after the fact proves nothing, since a forger
could equally mint one on demand. Run on a schedule, not by hand:

```bash
python scripts/checkpoint_cron.py
# 2026-08-21 02:20:34+00  root=505a7ef0...  leaves=59  id=144ebb48-...
```

```cron
0 * * * *  cd /path/to/agent-x && DATABASE_URL=... python scripts/checkpoint_cron.py >> checkpoint.log 2>&1
```

Pipe that line to somewhere outside this database — a git commit, a status page, a
timestamping service. A root stored only in this database's own `checkpoints` table
proves nothing, since that table could be rewritten too.

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
