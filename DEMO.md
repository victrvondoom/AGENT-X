# TrustDoc — 3-minute demo script

**The whole demo builds to one moment: handing the judge something they can check
themselves, and then getting out of the way.** Every beat before it exists to make that
last beat land. If you are running short, cut from the middle — never from the end.

Setup before recording: `docker compose up -d`, `uvicorn app.main:app`, browser at
`http://127.0.0.1:8000/trustdoc`, a second tab with the offline verifier saved to your
desktop, and a terminal ready with `psql`.

---

## 0:00 — 0:20 · The problem, in one sentence

> "This is an invoice that has to be processed correctly, because getting it wrong is a
> compliance failure, not a typo. Most document AI will read it and hand you an answer.
> It will not tell you which parts it was unsure about — and that is the only part that
> matters."

**On screen:** the messy invoice.

---

## 0:20 — 0:50 · Extraction, and the number that matters

Upload. Fields appear with confidences.

> "Nutrient DWS does the document work. It returns every field with a confidence signal
> and a citation back to the page region it came from. That per-field uncertainty is
> what everything here is built on."

**Point at `account_number`.**

> "Look at this one. Confidence nought-point-nine-nine. Legibility nought-point-four-one.
> The model is extremely confident about a bank account number it could barely see. A
> single threshold would have auto-accepted that. We hold it."

**Then `description`.**

> "And this one has no confidence at all. Nutrient's own docs say absent doesn't mean
> low — it means no signal. So it goes to a human too. Three fields cleared, three held."

---

## 0:50 — 1:25 · The human gate

> "Now the pipeline stops. Not slows down — stops."

Correct the total from `1,248.00` to `12,480.00`.

> "The extractor dropped a digit. I'm fixing it, and both values go on the record — what
> the machine said and what I changed it to."

Rule on the rest. The job flips to APPROVED.

> "That resume is a single guarded SQL statement. If you call our own internal function
> to skip it, it refuses. The gate is in the database, not in our good manners."

---

## 1:25 — 2:00 · Sign, then check your own work

Click **Generate & sign**.

> "It generates the document, signs it, and then does the part most pipelines skip: it
> re-reads the file it just produced and compares every field to what I approved."

**Point at `self-verify: PASS`.**

> "That's not a dict compared to itself. It parses the actual bytes. Flip one byte in
> that file and this read-back changes — there's a test that proves it."

*(If you have 20 seconds spare, run `demo_phase4.py` and show the corrupted run being
caught and marked FAILED.)*

---

## 2:00 — 2:50 · **The moment — hand it over**

Show the certificate. Then stop talking about your software.

> "Here's the certificate. Every field, how it was routed, who reviewed it, the hash of
> the signed document, and the head of the audit chain. Now — don't take my word for any
> of it."

**Beat 1 — the offline verifier.** Switch to the file already saved on your desktop.
**Turn your Wi-Fi off on camera.**

> "This is a file on my desktop. My network is off. Watch."

Paste the certificate. Both checks go green.

> "It recomputed the hash and verified the ECDSA signature in the browser. There is no
> server. Our code is not in this loop."

**Beat 2 — the SQL.** Switch to the terminal.

```bash
psql "$DATABASE_URL" -v job="'<uuid>'" -f db/verify_chain.sql
```

> "And this re-derives every hash in the audit chain from scratch, in SQL. Not reading
> our stored hashes back — recomputing them. `PASS`."

Now break it, live:

```sql
UPDATE audit_log SET detail_canonical = '{"engine":"forged"}' WHERE job_id = '<uuid>' AND seq = 1;
```

Re-run the query. `FAIL — a row was edited`.

> "One row changed. Every hash after it breaks. That's the regulator trail."

---

## 2:50 — 3:00 · Close on the limitation — and how it's closed

> "One thing I'll say plainly: a certificate carries the key its own signature is
> checked against, so on its own terms it can never prove it isn't a forgery. We close
> that with time, not more crypto — publish a Merkle root over every chain periodically.
> A real certificate proves it was included in a checkpoint dated before any dispute. A
> forgery made afterward can't be, without changing a root that's already public."

**Last line:**

> "Confidence-routed, human-gated, self-verified, checkable without us — and the one gap
> we couldn't close with cryptography, we closed with time. That's it."

---

## Optional extended beat (+40s) — the trust spine in 3D

If you have room past 3:00, this is the strongest visual in the product and it is real
data, not an animation. Open **`/spine`**.

> "This is the actual chain, pulled live from the database — erasure and document
> events interleaved, because they're one chain, not two ledgers."

Click **Tamper a block**.

> "Watch what happens to everything after it." *(cascades red, live; the Merkle root
> above visibly moves)*

Click **Crypto-shred**.

> "And this is Phase 7 — erase a subject, and their block goes hollow. Contents gone,
> links still hold. The chain still verifies. That's proof kept, personal data
> destroyed — which is the whole thesis of this product, animated."

---

## Notes for the recording

- **Do not skip the Wi-Fi-off beat.** It is the single most persuasive three seconds in
  the demo and it costs nothing.
- **Do not say "signed" without qualifying it** if `DWS_API_KEY` is unset. Without it the
  PDF is not a signed PDF — only the detached certificate signature exists, and the UI
  says so. Claiming otherwise is the one thing that would lose an enterprise judge.
- If running on recorded extraction, say so once and move on: *"extraction is a recorded
  DWS response here; everything after it is live."* The audit chain records it as
  `recorded-fixture` anyway, so a judge who looks will find it — better they hear it
  from you.
- Ending on the limitation reads as confidence, not weakness. Every other demo in the
  room will end on a feature.
