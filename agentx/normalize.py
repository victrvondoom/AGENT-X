"""
Normalisation — turning what people and documents write into things that compare.

Everything downstream of here compares values: two charges are a duplicate if the
amounts are equal, a contradiction exists if a receipt and a statement disagree, a
deadline has passed if one date is later than another. None of those comparisons
work on raw text, and all of them are wrong in an interesting way if normalisation
is sloppy:

  * money is compared in MINOR UNITS as integers. Floating point loses cents on
    exactly the values a refund is measured in, and "2,399.00" versus "2399" is a
    formatting difference, not a discrepancy;
  * currency comes from the symbol or code when present and is otherwise UNKNOWN,
    never defaulted. Assuming USD is how a ₹2,399 dispute becomes a $2,399 one;
  * dates normalise to ISO, and an ambiguous numeric date (03/04/2026) is reported
    as ambiguous rather than resolved by locale guesswork — a date that decides
    whether a 14-day window closed is not a place for a coin flip.

The module is deterministic and has no LLM in it on purpose. It is the layer whose
output ends up inside a signed receipt.
"""
from __future__ import annotations

import re
from datetime import datetime

CURRENCY_SYMBOLS = {
    "₹": "INR", "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY",
    "A$": "AUD", "C$": "CAD", "S$": "SGD", "R$": "BRL", "₩": "KRW", "₽": "RUB",
}
CURRENCY_CODES = {"INR", "USD", "GBP", "EUR", "JPY", "AUD", "CAD", "SGD", "AED",
                  "CHF", "SEK", "NOK", "DKK", "PLN", "ZAR", "BRL", "MXN", "NZD",
                  "HKD", "CNY", "KRW"}
# Currencies whose smallest unit is the whole unit; a "cents" assumption
# would inflate every amount by a hundred.
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK"}

_AMOUNT = re.compile(
    # The comma-grouped alternative requires AT LEAST ONE group (`+`, not `*`).
    # With `*` a plain 4-digit run like "5000" matched the grouped alternative
    # against just its first 3 digits ("500") and stopped there — Python's regex
    # engine takes the first alternative that succeeds at a position and does not
    # backtrack to try a longer one once the rest of the pattern is satisfied by
    # optional groups. Requiring `+` forces "5000" to fall through to the plain
    # digit-run alternative, which matches it whole.
    r"(?P<sym>[₹$£€¥]|A\$|C\$|S\$|R\$)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"\s?(?P<code>INR|USD|GBP|EUR|JPY|AUD|CAD|SGD|AED|CHF|RS|RUPEES|DOLLARS|POUNDS|EUROS)?",
    re.IGNORECASE)

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUM_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")
_TEXT_DATE = re.compile(
    r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", re.I)
_TEXT_DATE_2 = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})\s*(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Reference shapes, most specific first. Order matters: an airline PNR and a
# generic order id overlap, and matching the generic pattern first would label
# every booking reference as an order.
REFERENCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("booking", re.compile(r"\b(?:PNR|booking(?:\s+(?:ref(?:erence)?|id|no\.?|number))?|"
                           r"confirmation(?:\s+(?:code|number|no\.?))?|reservation)"
                           r"\s*[:#-]?\s*([A-Z0-9]{5,12})\b", re.I)),
    ("order", re.compile(r"\b(?:order(?:\s+(?:id|no\.?|number|ref))?|invoice(?:\s+no\.?)?)"
                         r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{4,24})\b", re.I)),
    ("shipment", re.compile(r"\b(?:tracking(?:\s+(?:no\.?|number|id))?|awb|consignment)"
                            r"\s*[:#-]?\s*([A-Z0-9]{8,25})\b", re.I)),
    ("policy_number", re.compile(r"\b(?:policy(?:\s+(?:no\.?|number))?)"
                                 r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{4,20})\b", re.I)),
    ("case_ref", re.compile(r"\b(?:case|ticket|ref(?:erence)?|complaint)"
                            r"\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{4,20})\b", re.I)),
]

_CARD_LAST4 = re.compile(r"(?:ending(?:\s+in)?|last\s*4|xxxx|\*{4})\s*[-\s]?(\d{4})\b", re.I)
_FLIGHT = re.compile(r"\b([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(\d{1,4})\b")


def money(text: str, *, default_currency: str | None = None) -> dict | None:
    """First money value in `text`, as minor units plus a currency (or None).

    Returns None rather than guessing when there is no number at all. `currency`
    is None when nothing in the text says which one — the caller decides whether
    that is fatal, and for a refund it usually is.
    """
    if not text:
        return None
    m = _AMOUNT.search(text)
    if not m or not m.group("num"):
        return None
    raw = m.group("num").replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None

    cur = None
    sym, code = m.group("sym"), (m.group("code") or "").upper()
    if sym:
        cur = CURRENCY_SYMBOLS.get(sym)
    if not cur and code:
        cur = {"RS": "INR", "RUPEES": "INR", "DOLLARS": "USD",
               "POUNDS": "GBP", "EUROS": "EUR"}.get(code, code if code in CURRENCY_CODES else None)
    if not cur:
        for c in CURRENCY_CODES:
            if re.search(rf"\b{c}\b", text, re.I):
                cur = c
                break
    cur = cur or default_currency

    exponent = 0 if (cur in ZERO_DECIMAL) else 2
    minor = int(round(value * (10 ** exponent)))
    return {"minor": minor, "currency": cur, "display": raw,
            "exponent": exponent, "text": m.group(0).strip()}


def all_money(text: str, *, default_currency: str | None = None) -> list[dict]:
    """Every money value in `text`, in order of appearance."""
    out, seen = [], set()
    for m in _AMOUNT.finditer(text or ""):
        span = m.group(0).strip()
        if not m.group("num") or not re.search(r"\d", span):
            continue
        parsed = money(span, default_currency=default_currency)
        if not parsed:
            continue
        # A bare integer with no symbol, no code and no decimals is far more
        # likely to be a quantity, a year or a reference fragment than a price.
        if parsed["currency"] is None and "." not in span and "," not in span \
                and not m.group("sym"):
            continue
        key = (parsed["minor"], parsed["currency"], m.start())
        if key in seen:
            continue
        seen.add(key)
        parsed["offset"] = m.start()
        out.append(parsed)
    return out


def fmt_money(minor: int | None, currency: str | None) -> str:
    """Human rendering. Used in receipts and approval prompts, so it must never
    invent a currency it was not given."""
    if minor is None:
        return "—"
    exponent = 0 if currency in ZERO_DECIMAL else 2
    major = minor / (10 ** exponent)
    sym = {v: k for k, v in CURRENCY_SYMBOLS.items()}.get(currency or "", "")
    body = f"{major:,.{exponent}f}"
    if sym:
        return f"{sym}{body}"
    return f"{body} {currency}" if currency else body


def date(text: str) -> dict | None:
    """Normalise the first date in `text`.

    `ambiguous` is set when a numeric date could be read either day-first or
    month-first and the numbers do not settle it. Downstream that becomes a
    question to the user, because a 14-day cancellation window is decided by which
    reading is right.
    """
    if not text:
        return None
    m = _ISO_DATE.search(text)
    if m:
        y, mo, d = map(int, m.groups())
        return _mk(y, mo, d, False, m.group(0))

    m = _TEXT_DATE.search(text)
    if m:
        d, mon, y = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()], int(m.group(3))
        return _mk(y, mon, d, False, m.group(0))

    m = _TEXT_DATE_2.search(text)
    if m:
        mon, d, y = _MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
        return _mk(y, mon, d, False, m.group(0))

    m = _NUM_DATE.search(text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        if a > 12 and b <= 12:
            return _mk(y, b, a, False, m.group(0))
        if b > 12 and a <= 12:
            return _mk(y, a, b, False, m.group(0))
        # both <= 12: genuinely ambiguous. Day-first is reported as the primary
        # reading because it is the majority convention worldwide, and the
        # ambiguity flag is what makes that choice safe to make.
        return _mk(y, b, a, True, m.group(0))
    return None


def _mk(y: int, mo: int, d: int, ambiguous: bool, raw: str) -> dict | None:
    try:
        dt = datetime(y, mo, d)
    except ValueError:
        return None
    return {"iso": dt.strftime("%Y-%m-%d"), "ambiguous": ambiguous, "text": raw}


# Words that follow a reference keyword often enough to be captured as one.
# "Order  Total charged 2,399" yields "TOTAL" without this, and a fact graph with
# two order ids in it produces a blocking contradiction that stops the case dead —
# which is how a cosmetic extraction bug becomes a refusal to act.
_NOT_A_REFERENCE = {
    "TOTAL", "AMOUNT", "NUMBER", "DATE", "STATUS", "CONFIRMATION", "ORDER",
    "BOOKING", "INVOICE", "RECEIPT", "PAYMENT", "REFUND", "DETAILS", "SUMMARY",
    "CHARGED", "PLACED", "ITEM", "ITEMS", "CUSTOMER", "ACCOUNT", "REFERENCE",
    "TRACKING", "SHIPPED", "DELIVERED", "PENDING", "COMPLETED", "CANCELLED",
}


def _looks_like_reference(value: str) -> bool:
    """Is this token plausibly an identifier rather than a word?

    Requiring a digit is the single most effective filter: real order, booking and
    policy references essentially always contain one, and English words never do.
    An all-letter token that survives that check would be a word.
    """
    v = (value or "").upper()
    if v in _NOT_A_REFERENCE or len(v) < 5:
        return False
    if not any(ch.isdigit() for ch in v):
        return False
    if v.isdigit() and len(v) < 6:
        return False
    return True


def references(text: str) -> list[dict]:
    """Typed references (booking, order, shipment, policy, case) found in text."""
    out, claimed = [], set()
    for kind, pat in REFERENCE_PATTERNS:
        for m in pat.finditer(text or ""):
            val = m.group(1).upper()
            if val in claimed or not _looks_like_reference(val):
                continue
            claimed.add(val)
            out.append({"kind": kind, "value": val, "text": m.group(0).strip()})
    return out


def card_last4(text: str) -> str | None:
    m = _CARD_LAST4.search(text or "")
    return m.group(1) if m else None


def flight_number(text: str) -> str | None:
    """An IATA-shaped flight designator, filtered against obvious false hits.

    The pattern matches a lot of ordinary text ("AB 12"), so a match only counts
    when the surrounding text is actually talking about a flight.
    """
    if not re.search(r"\bflight|\bPNR\b|boarding", text or "", re.I):
        return None
    m = _FLIGHT.search((text or "").upper())
    return f"{m.group(1)}{m.group(2)}" if m else None


def canon(value) -> str:
    """The comparison form of a value: case-folded, whitespace-collapsed."""
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()
