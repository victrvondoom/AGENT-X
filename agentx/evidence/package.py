"""
The evidence package — a portable, independently checkable case file.

This is what a consumer hands to a merchant's disputes team, attaches to a card
issuer's chargeback form, or gives to an ombudsman. It has to survive leaving
Agent X entirely, which imposes three requirements that shape the whole format:

  1. **Every claim resolves to a document the holder already has.** Facts carry
     their evidence ids, and evidence carries the sha256 of the RAW bytes the user
     uploaded. The recipient re-hashes their own copy of the receipt and matches
     it against the package. No server involved.

  2. **The package is hashed and signed over canonical bytes.** Same
     canonicalisation as the erasure certificate — sorted keys, no incidental
     whitespace, UTF-8 — so a verifier that can check one can check the other, and
     `templates/verify_offline.html` needs no new code path.

  3. **Contradictions travel with it.** A package that quietly dropped the
     disagreement between the receipt and the statement would be a better-looking
     document and a worse piece of evidence. Anyone reading it is entitled to see
     what Agent X could not reconcile.

WHAT THE SIGNATURE DOES AND DOES NOT PROVE

It proves this package was issued by the holder of Agent X's signing key and has not
been altered since. It does not, on its own, prove the package is genuine, because
the public key travels inside it — the same limitation the erasure certificate
documents. Two things close that gap and the package supports both: pin the key
against the published one, or check the `chain_head` against the case's live chain,
which a forgery cannot reproduce.
"""
from __future__ import annotations

import base64
import hashlib

from core.trust import certificate
from agentx import chain, ids, normalize, store
from agentx.evidence import contradiction, graph

SPEC = "agentx-evidence-package/v1"

# What a package is FOR changes what belongs in it. A card issuer wants the
# transaction lines and the merchant contact attempts; an ombudsman wants the
# policy analysis and the correspondence. Same facts, different emphasis — and
# saying which audience a package was built for is part of being honest about it.
AUDIENCES = {
    "merchant_dispute": "The merchant's own disputes or customer resolution team",
    "payment_dispute": "The card issuer or payment provider handling a chargeback",
    "insurance_claim": "An insurer assessing a claim",
    "regulator": "A regulator or ombudsman reviewing an unresolved complaint",
    "human_review": "A person checking Agent X's work before it goes anywhere",
    "audit": "An auditor verifying what the agent did and on what basis",
}


def build(conn, case_id: str, *, audience: str = "human_review",
          claims: list | None = None) -> dict:
    """Assemble the package body. Unsigned — `sign()` adds the attestation."""
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}; one of {sorted(AUDIENCES)}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_ref, title, description, domain, problem_type, confidence,"
            " state, resolution, amount_minor, currency, created_at, updated_at"
            " FROM cases WHERE id = %s", (case_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"no such case {case_id}")
    cols = ["id", "user_ref", "title", "description", "domain", "problem_type",
            "confidence", "state", "resolution", "amount_minor", "currency",
            "created_at", "updated_at"]
    case = dict(zip(cols, row))

    ev = graph.list_evidence(conn, case_id)
    facts = graph.facts_for(conn, case_id, active_only=False)
    links = graph.links_for(conn, [f["id"] for f in facts])
    cons = contradiction.open_contradictions(conn, case_id)
    ch = chain.verify(conn, case_id)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT policy_id, title, authority, jurisdiction, applies, because, citation"
            " FROM case_policies WHERE case_id = %s ORDER BY applies DESC, policy_id",
            (case_id,))
        pol_cols = ["policy_id", "title", "authority", "jurisdiction", "applies",
                    "because", "citation"]
        policies = [dict(zip(pol_cols, r)) for r in cur.fetchall()]

        cur.execute(
            "SELECT id, action, provider, provider_mode, state, external_ref, verified,"
            " requested_at, finished_at, error FROM executions WHERE case_id = %s"
            " ORDER BY requested_at ASC", (case_id,))
        ex_cols = ["id", "action", "provider", "provider_mode", "state", "external_ref",
                   "verified", "requested_at", "finished_at", "error"]
        executions = [dict(zip(ex_cols, r)) for r in cur.fetchall()]

        cur.execute(
            "SELECT id, direction, channel, counterparty, subject, external_ref, sha256,"
            " sent_at FROM communications WHERE case_id = %s ORDER BY sent_at ASC",
            (case_id,))
        cm_cols = ["id", "direction", "channel", "counterparty", "subject",
                   "external_ref", "sha256", "sent_at"]
        comms = [dict(zip(cm_cols, r)) for r in cur.fetchall()]

    return {
        "spec": SPEC,
        "issued_at": ids.now(),
        "audience": audience,
        "audience_note": AUDIENCES[audience],
        "case": {
            "id": case["id"], "opened": case["created_at"], "state": case["state"],
            "problem_type": case["problem_type"], "domain": case["domain"],
            "confidence": case["confidence"], "resolution": case["resolution"],
            "amount": normalize.fmt_money(case["amount_minor"], case["currency"]),
            "amount_minor": case["amount_minor"], "currency": case["currency"],
            "summary": case["title"] or (case["description"] or "")[:180],
        },
        "engine": store.describe(),
        "evidence": [
            {"id": e["id"], "kind": e["kind"], "filename": e["filename"],
             "sha256": e["sha256"], "bytes": e["bytes"], "trust": e["trust"],
             "captured_at": e["captured_at"]}
            for e in ev
        ],
        "facts": [
            {"id": f["id"], "predicate": f["predicate"], "value": f["value_text"],
             "unit": f["unit"], "confidence": f["confidence"], "method": f["method"],
             "status": f["status"],
             "sources": [{"evidence_id": l["evidence_id"], "locator": l["locator"],
                          "excerpt": (l["excerpt"] or "")[:200]}
                         for l in links.get(f["id"], [])]}
            for f in facts
        ],
        "claims": [c.as_dict() if hasattr(c, "as_dict") else c for c in (claims or [])],
        "contradictions": [
            {"predicate": c["predicate"], "severity": c["severity"],
             "detail": c["detail"], "status": c["status"],
             "facts": [c["fact_a"], c["fact_b"]]}
            for c in cons
        ],
        "policies": policies,
        "actions_taken": executions,
        "communications": comms,
        "chain": {"head": ch.get("head"), "length": ch.get("rows"),
                  "intact_at_issue": ch.get("ok"),
                  "content_digest": chain.digest(conn, case_id)},
        "how_to_verify": [
            "Recompute sha256 over the canonical JSON of `package` "
            "(sorted keys, separators ',' and ':', UTF-8, no whitespace) and compare "
            "to the `sha256` field of this envelope.",
            "Verify `signature` (ECDSA P-256 over those same bytes) against "
            "`public_key`. Pin the key against Agent X's published key to rule out a "
            "self-signed forgery.",
            "For each evidence item, re-hash your own copy of the file and compare "
            "to its `sha256`. A match proves the package refers to the document you "
            "hold; a mismatch proves it does not.",
            "Ask Agent X for the live chain head of this case and compare it to "
            "`chain.head`. A package whose head does not match the live chain was "
            "not issued from this case's record.",
        ],
        "limitations": [
            "Facts marked method=llm were read by a language model and are capped "
            "below deterministic extraction; each still quotes the source text it "
            "was read from.",
            "Open contradictions are listed rather than resolved. Agent X does not "
            "choose between disagreeing sources.",
            "Policy analysis is an engineering artefact, not legal advice.",
        ],
    }


def sign(package: dict, private_key=None) -> dict:
    """Hash and sign a package. Reuses the certificate envelope format verbatim."""
    env = certificate.sign(package, private_key)
    # The trust spine calls the signed object `certificate`; a consumer-facing
    # package is not a certificate, so it is renamed while the bytes that were
    # hashed stay exactly the same.
    env["package"] = env.pop("certificate")
    env["spec"] = SPEC
    return env


def verify(env: dict, conn=None, trusted_public_key: str | None = None) -> dict:
    """Check a package. Hash and signature need nothing but the package itself."""
    pkg = env.get("package")
    if not isinstance(pkg, dict):
        return {"ok": False, "error": "envelope has no `package` object", "checks": {}}

    shim = {**env, "certificate": pkg}
    out = certificate.verify(shim, conn=None, trusted_public_key=trusted_public_key)

    if conn is not None and pkg.get("case", {}).get("id"):
        # Inclusion, not exact-tip matching — see `chain.verify_inclusion` for why:
        # issuing this very package appends a row to the case it describes, so the
        # live chain is always at least one row ahead of what was attested.
        attested = pkg.get("chain", {})
        length = attested.get("length")
        seq = (length - 1) if isinstance(length, int) and length > 0 else None
        out["checks"]["case_chain"] = chain.verify_inclusion(
            conn, pkg["case"]["id"], seq=seq, content_hash=attested.get("head"))
    else:
        out["checks"]["case_chain"] = {"ok": None,
                                       "detail": "not checked (offline verification)"}

    decided = [c["ok"] for c in out["checks"].values() if c["ok"] is not None]
    out["ok"] = bool(decided) and all(decided)
    return out


def verify_evidence_file(env: dict, evidence_id: str, raw: bytes) -> dict:
    """Does this file match what the package says it is?

    The check a merchant actually runs: they have the receipt, the package claims
    a hash for it, and either the bytes agree or the package is not about this
    document.
    """
    pkg = env.get("package") or {}
    for e in pkg.get("evidence") or []:
        if e.get("id") == evidence_id:
            got = hashlib.sha256(raw).hexdigest()
            return {"ok": got == e.get("sha256"), "expected": e.get("sha256"),
                    "actual": got, "kind": e.get("kind"),
                    "detail": ("the file matches the package" if got == e.get("sha256")
                               else "this file is NOT the one the package refers to")}
    return {"ok": False, "detail": f"the package contains no evidence item {evidence_id!r}"}


def store_package(conn, case_id: str, env: dict) -> dict:
    """Persist a signed package alongside its case, so it can be re-served
    byte-identically rather than rebuilt (a rebuild would carry a new
    `issued_at` and therefore a different hash, breaking anyone's saved copy)."""
    rid = ids.new("pkg")
    pkg = env.get("package") or {}
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO receipts (id, case_id, envelope, sha256, chain_head,"
            " chain_length, signed, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (rid, case_id, store.jdump(env), env.get("sha256", ""),
             (pkg.get("chain") or {}).get("head") or "",
             int((pkg.get("chain") or {}).get("length") or 0),
             bool(env.get("signed")), ids.now()))
    return {"id": rid, "sha256": env.get("sha256"), "signed": bool(env.get("signed"))}
