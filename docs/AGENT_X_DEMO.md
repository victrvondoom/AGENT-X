# Agent X — 3-minute demo script

**The whole demo builds to one moment: a company says no, Agent X escalates on its
own initiative days later, and the receipt at the end is something the judge can
check without trusting a word we said.** Every beat before it exists to set that
moment up.

Setup before recording: `uvicorn app.main:app --port 8080`, browser at
`http://127.0.0.1:8080/agentx`, `POST /api/agentx/demo/reset` run once so the
sandbox starts clean. No database, no API keys, and no internet connection are
required — everything below runs on the local SQLite engine against the
deterministic sandbox.

---

## 0:00 — 0:20 · The problem, in one sentence

> "Consumers have hundreds of fragmented systems for solving problems — a
> merchant's chat bot, a bank's dispute form, an airline's claims portal — and
> none of them prove anything. Agent X turns any consumer problem into one case: it
> investigates, plans, acts, follows up, and verifies — and hands you a signed
> receipt at the end you don't have to take our word for."

**On screen:** the Agent X home page — just a text box: *"What happened?"*

---

## 0:20 — 0:55 · Ambiguity, held rather than guessed

Type: *"They charged me again."*

> "This sentence is the whole product thesis. It's consistent with a duplicate
> charge, a subscription renewal, a card pre-authorisation, an instalment plan, a
> corrected invoice, or fraud — and most AI products pick one and act on it. Agent X
> doesn't. It holds six live interpretations and asks the ONE question that
> would separate them, ranked by how much it would actually tell us."

**Point at the live preview panel** showing the six chips and the ranked
question.

> "Not a form. One question, chosen because it's worth the most information."

---

## 0:55 — 1:40 · A real case, with real evidence

Clear the box. Type the duplicate-charge scenario (or click **Scenario A** in
"See it work"):

> "Kartly charged me twice for the same order. Two charges of 2,399 rupees on
> August 2nd. Order 402-9938271."

Attach the bank statement and the order confirmation.

> "Agent X reads both documents, extracts the charge and the order total as typed
> facts — each one with a locator back to the exact line it came from — and
> checks whether the two documents actually agree. They do here. When they
> don't, Agent X doesn't average them. It marks the value contested and refuses to
> act on it until a human says which one is right."

Point at the policy section: **card scheme chargeback = yes**, **s.75 = no**
(amount below threshold), **RBI harmonisation = yes**.

> "Every rule is evaluated deterministically against the facts — not asked of a
> language model. `yes`, `no`, or `unknown` when a fact isn't established yet,
> never a guess."

---

## 1:40 — 2:15 · Approve, and watch it get refused

Click **Review resolution** → the plan: read terms, draft, ask for the refund,
wait, chase, escalate, verify, issue receipt — with the branch shown.

> "This is a validated execution graph, not a paragraph an LLM wrote. Every step
> is checked before it's shown to you: does a provider exist for it, is the
> capability at the right autonomy level, does every external action have a
> verification step after it. A plan that fails any of those checks never
> reaches this screen."

Click **Approve** on the refund request.

> "Agent X sends it — labelled `sandbox` everywhere, because Kartly here is a
> deterministic sandbox company, not a real one, and that label travels all the
> way to the receipt."

**Advance the clock twice** (`+6 days`, `+6 days` — buttons on the demo panel).

> "Kartly stalls behind 'under review.' Agent X chases automatically on the clock
> it was told to wait — this isn't a cron job that fires blindly, it re-reads the
> case first and only fires if the case is still actually waiting. After the
> second chase gets nothing, it recommends escalation and — because this case is
> below the autonomy level that allows escalating unattended — it ASKS."

Approve the escalation.

> "Escalation goes to the payment provider. It's approved, and here's the part
> that matters: Agent X doesn't take Kartly's word for it. It calls back out and
> reads Kartly's own payment ledger to confirm the credit actually posted before
> it says 'resolved.'"

---

## 2:15 — 2:50 · The receipt, checked live

Open **Proof → Show the receipt**.

> "Problem, finding, evidence, action, external reference, result, verification
> state — and it never says 'confirmed' unless a re-read of the company's own
> records backed it up. Below that: a hash-linked, ECDSA-signed attestation."

Click **Verify it**.

> "Recompute the hash, check the signature, and check that this receipt's
> attested position is actually still in the case's live chain — all three,
> independently, right here."

**Optional, if there's time — Erase this case.**

> "And because this is built on Agent X's trust spine underneath, the same
> crypto-shred that makes Agent X's erasure provable works per-case here. Destroy
> the key — the content becomes unrecoverable — and the chain still verifies,
> because the ciphertext was hashed, and it was never touched."

---

## 2:50 — 3:00 · Close

> "Five of these run end to end in the repo, unscripted — a stalling merchant, a
> retention script that folds when a cited right lands, a hotel that pays the
> rate and fights the difference, a statutory air-passenger claim computed from
> distance and delay minutes, and the easy one that resolves in a single pass.
> Same eleven-stage pipeline for all five. What changes between them is a YAML
> file and which sandbox company answers — never the code."

---

## If you're running short

Cut the erase-this-case beat first, then the ambiguity beat — but never cut the
receipt-verification beat. That's the moment the whole demo exists to set up.
