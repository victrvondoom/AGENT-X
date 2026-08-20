"""
The confidence gate — Pattern 1, and the shared human-routing primitive.

Every pipeline in this product routes work between a machine and a person the same
way: something produces a value with a quality signal, this decides whether that
signal is good enough to accept unattended, and anything that isn't goes to a human.
The document pipeline gates extracted fields; the erasure pipeline gates on legal
hold. Same idea, same audit vocabulary, one place to reason about risk.

WHY THIS IS NOT A SINGLE 0.85 THRESHOLD
---------------------------------------
Nutrient's own extraction documentation is explicit on two points:

  * confidence is "relative and uncalibrated; it isn't a probability or percentage"
  * "An absent confidence value means that no score was available. It doesn't mean
    low confidence."

Both break the obvious implementation.

Comparing an uncalibrated score against a fixed 0.85 implies a precision the number
does not have — 0.85 on an invoice total and 0.85 on a vendor name are not the same
evidence. So thresholds are per field-type and, more importantly, are DECLARED: the
policy is data, it ships in the audit trail, and a judge can read exactly what rule
routed each field instead of taking a magic constant on faith.

And treating absent as 0.0 floods the review queue, while treating it as 1.0
silently auto-accepts unscored fields — which in a compliance product is the worse
of the two failures. Absent is therefore its own branch with its own reason code, so
the audit trail can distinguish "the model was unsure" from "the model said nothing".
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

# Default when a field type has no explicit policy. Matches the spec's 0.85, but it
# is a floor for unclassified fields rather than a universal truth.
DEFAULT_THRESHOLD = 0.85

# Money, identifiers and dates are the fields that cause real-world harm when wrong,
# so they are held to a higher bar. Descriptive fields are cheaper to get wrong and
# expensive to review, so they sit lower. These numbers are a POLICY, not a
# measurement — they ship in the certificate so a reviewer can argue with them.
FIELD_THRESHOLDS: dict[str, float] = {
    "total":          0.95,
    "amount":         0.95,
    "tax":            0.95,
    "account_number": 0.97,
    "iban":           0.97,
    "id_number":      0.97,
    "date":           0.92,
    "due_date":       0.92,
    "invoice_number": 0.92,
    "vendor_name":    0.80,
    "address":        0.75,
    "description":    0.70,
}

# OCR legibility gates independently of extraction confidence: a model can be very
# sure it read a number correctly off a source region that is, in fact, mush.
RECOGNITION_FLOOR = 0.60

AUTO, HUMAN = "AUTO", "HUMAN"


@dataclass(frozen=True)
class Decision:
    decision: str            # AUTO | HUMAN
    reason: str              # machine-readable, lands in the audit trail
    threshold: float | None  # what it was actually judged against
    explain: str             # one line a human reviewer can read

    def as_dict(self) -> dict:
        return asdict(self)


def threshold_for(field_name: str, overrides: dict[str, float] | None = None) -> float:
    """Threshold for a field, by exact name then by suffix.

    Suffix matching so `line_items[0].amount` inherits the `amount` policy rather
    than silently dropping to the default — extractors emit paths, not bare names,
    and a policy that only matches bare names would quietly under-protect every
    nested money field.
    """
    table = {**FIELD_THRESHOLDS, **(overrides or {})}
    key = (field_name or "").strip().lower()
    if key in table:
        return table[key]
    tail = key.rsplit(".", 1)[-1].rstrip("]").split("[")[0]
    return table.get(tail, DEFAULT_THRESHOLD)


def route(field_name: str,
          confidence: float | None,
          recognition: float | None = None,
          overrides: dict[str, float] | None = None) -> Decision:
    """Decide whether a single extracted field can be accepted unattended.

    Order matters: absence is checked before magnitude, and legibility before
    confidence, so the reason recorded is the FIRST reason the field is untrusted
    rather than an incidental one.
    """
    t = threshold_for(field_name, overrides)

    if confidence is None:
        return Decision(HUMAN, "no_confidence_signal", t,
                        "The extractor returned no confidence for this field. Absent is "
                        "not the same as low, so this is routed to a human rather than "
                        "guessed either way.")

    if not (0.0 <= float(confidence) <= 1.0):
        return Decision(HUMAN, "confidence_out_of_range", t,
                        f"Confidence {confidence!r} is outside 0..1 and cannot be "
                        f"compared to a threshold.")

    if recognition is not None and float(recognition) < RECOGNITION_FLOOR:
        return Decision(HUMAN, "illegible_source", t,
                        f"OCR legibility {float(recognition):.2f} is below "
                        f"{RECOGNITION_FLOOR:.2f}. The value may be confidently read "
                        f"off an unreadable region.")

    if float(confidence) < t:
        return Decision(HUMAN, "below_threshold", t,
                        f"Confidence {float(confidence):.2f} is below the {t:.2f} "
                        f"required for '{field_name}'.")

    return Decision(AUTO, "above_threshold", t,
                    f"Confidence {float(confidence):.2f} meets the {t:.2f} required "
                    f"for '{field_name}'.")


def route_all(fields: list[dict], overrides: dict[str, float] | None = None) -> list[dict]:
    """Route a whole extraction. Returns each field with its decision attached."""
    out = []
    for f in fields:
        d = route(f.get("name", ""), f.get("confidence"), f.get("recognition"), overrides)
        out.append({**f, **d.as_dict()})
    return out


def summarise(routed: list[dict]) -> dict:
    """Counts for the audit entry and the UI."""
    reasons: dict[str, int] = {}
    for f in routed:
        reasons[f["reason"]] = reasons.get(f["reason"], 0) + 1
    auto = sum(1 for f in routed if f["decision"] == AUTO)
    return {"total": len(routed), "auto": auto, "human": len(routed) - auto,
            "reasons": reasons, "policy": "per-field-type thresholds, absent-aware"}


def policy_snapshot(overrides: dict[str, float] | None = None) -> dict:
    """The exact policy used, for the audit trail and the certificate.

    Recording this is what lets a judge re-derive every routing decision months
    later without our code: the thresholds were not implicit in a binary, they are
    in the signed record.
    """
    return {
        "default_threshold": DEFAULT_THRESHOLD,
        "recognition_floor": RECOGNITION_FLOOR,
        "field_thresholds": {**FIELD_THRESHOLDS, **(overrides or {})},
        "absent_confidence": "routed to HUMAN (absent != low)",
    }
