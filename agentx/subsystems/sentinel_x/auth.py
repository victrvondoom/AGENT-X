"""Bearer-token authentication for the SENTINEL API.

This exists because of a specific, concrete problem: the Deployment Gate is
the point where a human takes responsibility for shipping a security patch,
and the evidence record names who did it. Without authentication, that name
was whatever string the client happened to send - so the single most
important claim the system makes ("a human reviewed and approved this") was
forgeable by anyone who could reach the port, and the audit trail recorded
the forgery as fact.

Configuration
------------
SENTINEL_API_TOKENS: comma-separated ``principal:token`` pairs, e.g.

    SENTINEL_API_TOKENS="alice@corp.example:tok_a1b2,bob@corp.example:tok_c3d4"

When set, every mutating endpoint requires ``Authorization: Bearer <token>``
and the matched *principal* - never a client-supplied field - is what gets
written into the decision record and the audit ledger.

When unset, the API runs in local-development mode: mutations are allowed
and attributed to a principal explicitly named ``local-dev (unauthenticated)``
so the resulting records are self-describing rather than quietly implying a
human identity that was never verified. GET /api/system-info reports this
state so the posture is visible instead of assumed.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException

UNAUTHENTICATED_PRINCIPAL = "local-dev (unauthenticated)"


def _token_table() -> dict[str, str]:
    """Parses SENTINEL_API_TOKENS into {token: principal}. Malformed entries
    are skipped rather than silently granting access."""
    raw = os.environ.get("SENTINEL_API_TOKENS", "").strip()
    if not raw:
        return {}
    table: dict[str, str] = {}
    for pair in raw.split(","):
        principal, sep, token = pair.strip().partition(":")
        if sep and principal.strip() and token.strip():
            table[token.strip()] = principal.strip()
    return table


def auth_enabled() -> bool:
    return bool(_token_table())


def resolve_principal(authorization: str | None) -> str:
    """Returns the authenticated principal, or raises 401.

    In local-development mode (no tokens configured) this returns the
    explicitly-labelled unauthenticated principal instead of raising, so the
    demo runs without setup while every record it produces still says so.
    """
    table = _token_table()
    if not table:
        return UNAUTHENTICATED_PRINCIPAL

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            401,
            "Missing bearer token. This SENTINEL instance has SENTINEL_API_TOKENS "
            "configured, so mutating endpoints require Authorization: Bearer <token>.",
        )

    presented = authorization.split(" ", 1)[1].strip()
    # Constant-time comparison against every configured token: a plain dict
    # lookup would leak validity through timing, and this table is small.
    for token, principal in table.items():
        if secrets.compare_digest(presented, token):
            return principal
    raise HTTPException(401, "Invalid bearer token.")


def require_principal(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency for endpoints that change state."""
    return resolve_principal(authorization)
