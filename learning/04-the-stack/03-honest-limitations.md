# Honest limitations

The pitch is "verifiable forgetting," so it matters to be precise about what is and
isn't guaranteed.

- **We erase the store, not the model's parametric memory.** Forgetting removes data
  from CockroachDB; it does not unlearn what an LLM already absorbed into its weights.
  That's why the answering prompt is strictly grounded and honesty is verified
  *behaviorally* — a forgotten subject returns "not on record."
- **Coreference is name-based.** Entities are merged by name (`INSERT … ON CONFLICT`).
  Distinct things that share a name can merge, and vice-versa. Ingesting a name that
  already exists overwrites that node's embedding — a poisoning vector we call out
  rather than hide.
- **The AS OF SYSTEM TIME window is bounded by the cluster GC window.** The append-only
  `erasure_events` table and the S3 certificate provide durability beyond it.
- **The `t_before` anchor precedes the deleting transaction by a microseconds-wide window.**
  That ordering is deliberate (it's what makes the proof read the real pre-delete state), but
  it means a document ingested in that sliver — between the anchor and the delete — would be
  erased yet not appear in the prior-existence proof. In practice nothing writes to a subject
  mid-erasure; we note it rather than hide it.
- **The certificate's HTML page is operator-facing and names the erased subject** (for your
  audit trail) and is currently unauthenticated. The *portable* certificate and its S3 copy
  carry only a salted hash. Put the app behind auth (`AGENT_X_AUTH_TOKEN`) before exposing
  certificate URLs publicly.
- **The certificate ID is a content hash, not by itself a signature.** Anyone can
  re-derive it to detect tampering; the certificate is *additionally* ECDSA-signed and,
  when AWS is configured, written to object-locked (WORM) S3.
- **The SSRF guard on the BYO-model endpoint is best-effort** (resolve-then-connect).

Naming these is the point: a project about provable deletion shouldn't overclaim.
