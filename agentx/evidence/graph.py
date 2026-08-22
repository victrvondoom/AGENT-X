"""
The fact graph — evidence in, traceable claims out.

Three node types and one rule.

    EVIDENCE    a raw artefact: bytes, a sha256 of those bytes, a trust class,
                and its text sealed under the case's key
    FACT        one normalised claim read out of evidence, with a confidence
    LINK        fact → evidence, with the locator and the excerpt it came from

The rule: **no fact without a link.** A fact with no evidence behind it cannot be
written here, which means every statement Agent X makes to a user, a merchant or a
card issuer terminates in a document the user still holds and can check. That is
the difference between an agent that cites and an agent that sounds like it cites.

Two design decisions worth defending:

  * Evidence is hashed BEFORE it is sealed. The sha256 in the record is of the raw
    bytes the user uploaded, so the user can re-hash their own copy years later
    and match it against a signed evidence package. Hashing the ciphertext would
    make the digest verifiable only by us, which defeats the purpose.

  * Claims are DERIVED, never stored as first-class rows. A claim is a sentence
    plus the fact ids that support it, and its confidence is recomputed from those
    facts every time. Storing a claim would let it drift away from its evidence,
    and a stale claim with a fresh-looking confidence is exactly the failure mode
    this layer exists to prevent.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from agentx import ids, sealing, store
from agentx.evidence.extract import TRUST_WEIGHT, FactCandidate
from agentx.ontology import EVIDENCE_KINDS


@dataclass
class Claim:
    """A statement Agent X is prepared to make, and what it rests on."""
    text: str
    predicate: str
    confidence: float
    fact_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    contested: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {"claim": self.text, "predicate": self.predicate,
                "confidence": round(self.confidence, 3), "facts": self.fact_ids,
                "evidence": self.evidence_ids, "contested": self.contested,
                "note": self.note}


# ─────────────────────────────────────────────────────────────────────────────
# evidence
# ─────────────────────────────────────────────────────────────────────────────
def add_evidence(conn, *, case_id: str, workspace: str, subject: str, kind: str,
                 text: str, raw: bytes | None = None, filename: str | None = None,
                 media_type: str | None = None, trust: str | None = None,
                 captured_at: str | None = None) -> dict:
    """Store one artefact. Returns its record (without the sealed content)."""
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind {kind!r}")
    payload = raw if raw is not None else (text or "").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    eid = ids.new("ev")
    sealed_text = sealing.seal(conn, workspace, subject, text or "")
    trust = trust or (EVIDENCE_KINDS[kind].get("trust") or "user_capture")
    row = {
        "id": eid, "case_id": case_id, "workspace": workspace, "subject": subject,
        "kind": kind, "filename": filename, "media_type": media_type,
        "sha256": digest, "bytes": len(payload), "content_enc": sealed_text,
        "text_len": len(text or ""), "trust": trust,
        "captured_at": captured_at or ids.now(), "created_at": ids.now(),
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO evidence_items (id, case_id, workspace, subject, kind, filename,"
            " media_type, sha256, bytes, content_enc, text_len, trust, captured_at, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (row["id"], row["case_id"], row["workspace"], row["subject"], row["kind"],
             row["filename"], row["media_type"], row["sha256"], row["bytes"],
             row["content_enc"], row["text_len"], row["trust"], row["captured_at"],
             row["created_at"]))
    out = dict(row)
    out.pop("content_enc")
    return out


def list_evidence(conn, case_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, filename, media_type, sha256, bytes, text_len, trust,"
            " captured_at, created_at FROM evidence_items WHERE case_id = %s"
            " ORDER BY created_at ASC", (case_id,))
        cols = ["id", "kind", "filename", "media_type", "sha256", "bytes", "text_len",
                "trust", "captured_at", "created_at"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def evidence_text(conn, evidence_id: str) -> str | None:
    """Unseal one artefact's text. None once the case has been crypto-shredded —
    which is a normal state, not an error."""
    with conn.cursor() as cur:
        cur.execute("SELECT workspace, subject, content_enc FROM evidence_items WHERE id = %s",
                    (evidence_id,))
        row = cur.fetchone()
    if not row:
        return None
    return sealing.unseal(conn, row[0], row[1], row[2])


def evidence_kinds(conn, case_id: str) -> tuple[str, ...]:
    with conn.cursor() as cur:
        cur.execute("SELECT kind FROM evidence_items WHERE case_id = %s", (case_id,))
        return tuple(r[0] for r in cur.fetchall())


# ─────────────────────────────────────────────────────────────────────────────
# facts and links
# ─────────────────────────────────────────────────────────────────────────────
def add_facts(conn, case_id: str, evidence_id: str,
              candidates: list[FactCandidate]) -> list[dict]:
    """Write fact rows and their provenance links, in one go.

    Both writes happen together because a fact without its link is the one shape
    this graph must never contain: it looks like evidence and is not.
    """
    written: list[dict] = []
    now = ids.now()
    with conn.cursor() as cur:
        for c in candidates:
            fid = ids.new("fact")
            cur.execute(
                "INSERT INTO evidence_facts (id, case_id, predicate, subject_ref, value_text,"
                " value_num, value_norm, unit, confidence, method, status, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s)",
                (fid, case_id, c.predicate, c.subject_ref, c.value_text, c.value_num,
                 c.value_norm, c.unit, c.confidence, c.method, now))
            cur.execute(
                "INSERT INTO evidence_links (id, case_id, fact_id, evidence_id, locator,"
                " excerpt, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (ids.new("lnk"), case_id, fid, evidence_id, c.locator,
                 (c.excerpt or "")[:400], now))
            written.append({"id": fid, "predicate": c.predicate, "value_text": c.value_text,
                            "value_num": c.value_num, "value_norm": c.value_norm,
                            "unit": c.unit, "confidence": c.confidence,
                            "method": c.method, "evidence_id": evidence_id,
                            "locator": c.locator, "decision": c.decision,
                            "reason": c.reason})
    return written


def add_stated_fact(conn, case_id: str, predicate: str, value: str, *,
                    confidence: float = 0.55, unit: str | None = None,
                    value_num: float | None = None,
                    evidence_id: str | None = None, note: str = "") -> dict:
    """Record something the USER told us, at user-stated confidence.

    Still requires an evidence anchor — the answer itself is stored as
    `statement_note` evidence by the caller — so the no-fact-without-a-link rule
    holds for things a person said as well as for things a document said.
    """
    fid = ids.new("fact")
    now = ids.now()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO evidence_facts (id, case_id, predicate, subject_ref, value_text,"
            " value_num, value_norm, unit, confidence, method, status, created_at)"
            " VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,'user_stated','ACTIVE',%s)",
            (fid, case_id, predicate, value, value_num,
             (str(value) or "").strip().lower(), unit, confidence, now))
        if evidence_id:
            cur.execute(
                "INSERT INTO evidence_links (id, case_id, fact_id, evidence_id, locator,"
                " excerpt, created_at) VALUES (%s,%s,%s,%s,'user answer',%s,%s)",
                (ids.new("lnk"), case_id, fid, evidence_id, (note or value)[:400], now))
    return {"id": fid, "predicate": predicate, "value_text": value,
            "confidence": confidence, "method": "user_stated"}


def facts_for(conn, case_id: str, predicate: str | None = None,
              active_only: bool = True) -> list[dict]:
    sql = ("SELECT f.id, f.predicate, f.value_text, f.value_num, f.value_norm, f.unit,"
           " f.confidence, f.method, f.status, f.created_at FROM evidence_facts f"
           " WHERE f.case_id = %s")
    params: list = [case_id]
    if predicate:
        sql += " AND f.predicate = %s"
        params.append(predicate)
    if active_only:
        sql += " AND f.status <> 'SUPERSEDED'"
    sql += " ORDER BY f.predicate, f.confidence DESC"
    cols = ["id", "predicate", "value_text", "value_num", "value_norm", "unit",
            "confidence", "method", "status", "created_at"]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def links_for(conn, fact_ids: list[str]) -> dict[str, list[dict]]:
    if not fact_ids:
        return {}
    out: dict[str, list[dict]] = {}
    with conn.cursor() as cur:
        # One statement per id rather than an IN-list: the id list is short, and
        # array binding is the one place the two engines genuinely diverge.
        for fid in fact_ids:
            cur.execute("SELECT fact_id, evidence_id, locator, excerpt FROM evidence_links"
                        " WHERE fact_id = %s", (fid,))
            for r in cur.fetchall():
                out.setdefault(r[0], []).append(
                    {"evidence_id": r[1], "locator": r[2], "excerpt": r[3]})
    return out


def fact_map(conn, case_id: str) -> dict:
    """predicate → best value, for the policy evaluator.

    "Best" is the highest-confidence ACTIVE fact. Where a predicate is CONTESTED,
    the value is still returned but the caller can see the contradiction — the
    policy engine treats a contested input as unknown rather than picking a side.
    """
    best: dict[str, dict] = {}
    for f in facts_for(conn, case_id):
        cur = best.get(f["predicate"])
        if cur is None or (f["confidence"] or 0) > (cur["confidence"] or 0):
            best[f["predicate"]] = f
    out: dict = {}
    for pred, f in best.items():
        if f["status"] == "CONTESTED":
            continue                    # a disputed input is not an input
        out[pred] = f["value_num"] if f["value_num"] is not None else f["value_text"]
    return out


def derived_facts(base: dict, *, now: str | None = None) -> dict:
    """Facts computed from other facts, kept separate from measured ones.

    `incident.days_ago` is the one every deadline test needs and no document
    contains. Computing it here rather than storing it means it can never go
    stale, which for a value that decides whether a 120-day window is open is the
    difference between a right and a missed one.
    """
    out = dict(base)
    for key in ("charge.date", "order.purchased_at", "invoice.date", "event.date",
                "booking.checkin", "cancellation.at"):
        if key in base:
            days = ids.days_between(str(base[key]), now or ids.now())
            if days is not None:
                out["incident.days_ago"] = round(days, 1)
                out["incident.date"] = base[key]
                break
    if "charge.amount" in out and isinstance(out["charge.amount"], (int, float)):
        # Policy thresholds (s.75's £100 floor) are written in major units,
        # because that is how the statute is written.
        out["charge.amount"] = float(out["charge.amount"]) / 100.0
    return out


# ─────────────────────────────────────────────────────────────────────────────
# claims
# ─────────────────────────────────────────────────────────────────────────────
def combine_confidence(facts: list[dict]) -> float:
    """Noisy-OR over independent supporting facts, weighted by trust class.

    Two independent documents saying the same thing is genuinely stronger evidence
    than one saying it twice, and noisy-OR is the standard way to say so without
    letting five weak sources add up to certainty. The cap at 0.99 is deliberate:
    nothing Agent X reads off a document is ever certain, and a receipt that says
    1.0 invites a user to stop checking.
    """
    if not facts:
        return 0.0
    residual = 1.0
    for f in facts:
        w = TRUST_WEIGHT.get(f.get("trust") or "issuer_document", 0.85)
        residual *= (1.0 - min(0.98, float(f.get("confidence") or 0.0) * w))
    return round(min(0.99, 1.0 - residual), 3)


def build_claim(conn, case_id: str, predicate: str, text: str,
                contested_predicates: set[str] | None = None) -> Claim | None:
    """A claim about one predicate, carrying every fact that supports it."""
    rows = facts_for(conn, case_id, predicate)
    if not rows:
        return None
    links = links_for(conn, [r["id"] for r in rows])
    ev_ids: list[str] = []
    ev_trust: dict[str, str] = {}
    with conn.cursor() as cur:
        for r in rows:
            for l in links.get(r["id"], []):
                if l["evidence_id"] not in ev_ids:
                    ev_ids.append(l["evidence_id"])
                cur.execute("SELECT trust FROM evidence_items WHERE id = %s",
                            (l["evidence_id"],))
                got = cur.fetchone()
                if got:
                    ev_trust[r["id"]] = got[0]
    enriched = [{**r, "trust": ev_trust.get(r["id"], "user_capture")} for r in rows]
    contested = predicate in (contested_predicates or set()) or \
        any(r["status"] == "CONTESTED" for r in rows)
    conf = combine_confidence(enriched)
    if contested:
        # A contested predicate cannot support a confident claim, whatever the
        # individual readings say. Halving rather than zeroing keeps the claim
        # visible so a human can adjudicate it.
        conf = round(min(conf, 0.5), 3)
    return Claim(text=text, predicate=predicate, confidence=conf,
                 fact_ids=[r["id"] for r in rows], evidence_ids=ev_ids,
                 contested=contested,
                 note="a contradiction is open on this predicate" if contested else "")


def supersede(conn, fact_id: str, reason: str) -> None:
    """Retire a fact without deleting it. History is the product."""
    with conn.cursor() as cur:
        cur.execute("UPDATE evidence_facts SET status = 'SUPERSEDED' WHERE id = %s",
                    (fact_id,))
