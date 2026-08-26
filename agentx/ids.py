"""
Identifiers and clocks.

Two small things that everything else depends on, kept in one place because both
end up inside hashed records and must therefore be boring and stable.

  * IDs are minted by the application, never by a database default, so a case
    written on one engine reads identically on the other and an identifier inside
    a hash chain does not depend on which server produced it.
  * Time is always ISO-8601 UTC with a trailing Z, at second resolution. It sorts
    lexicographically in the same order it sorts chronologically, which is what
    lets deadline comparison work on both a real timestamptz column and a SQLite
    text column with no dialect branch.

Case IDs are short and human-sayable (PX-04182) because a consumer reads one out
over the phone to a call centre. Everything else is a typed random hex — long
enough to never collide, prefixed so a stray id in a log says what it is.
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta, timezone

_ALPHABET = "0123456789"


def now() -> str:
    """Current instant, ISO-8601 UTC, second resolution."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(ts: str | datetime | None) -> datetime | None:
    """Parse a timestamp, always returning it timezone-aware.

    A bare date ("2026-08-02") comes out of document extraction constantly and
    parses to a naive datetime; subtracting one from an aware one raises. Assuming
    UTC for a naive value is the only choice that keeps day arithmetic working, and
    day arithmetic is what decides whether a statutory window is still open.
    """
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def in_days(days: float, *, frm: str | None = None) -> str:
    """An instant `days` from now (or from `frm`), same format as now()."""
    base = parse(frm) or datetime.now(timezone.utc)
    return (base + timedelta(days=days)).replace(microsecond=0) \
        .astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def days_between(a: str | None, b: str | None) -> float | None:
    da, db = parse(a), parse(b)
    if not da or not db:
        return None
    return (db - da).total_seconds() / 86400.0


def case_id() -> str:
    """PX-NNNNN — short enough to read aloud, random enough not to be guessable
    in bulk. Uniqueness is enforced by the primary key, not by hope: the caller
    retries on conflict."""
    return "PX-" + "".join(random.choice(_ALPHABET) for _ in range(5))


def new(prefix: str, n: int = 12) -> str:
    """A typed random identifier, e.g. ev-3f8a91c2b704."""
    return f"{prefix}-{os.urandom(n // 2).hex()}"


def monotonic_suffix() -> str:
    """Millisecond suffix for ids that benefit from being time-ordered in a listing."""
    return format(int(time.time() * 1000), "x")
