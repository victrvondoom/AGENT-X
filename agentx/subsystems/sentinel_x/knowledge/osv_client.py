"""OSV.dev client — open-source vulnerability database, free, no auth required.
POST /v1/query resolves package+ecosystem+version to real vulnerability records.
"""

from __future__ import annotations

import requests
from typing import Any

BASE_URL = "https://api.osv.dev/v1"
TIMEOUT = 30


def query_osv(package: str, ecosystem: str, version: str | None = None) -> dict[str, Any]:
    """Query OSV.dev for vulnerabilities affecting a package.

    Args:
        package: Package name (e.g., "jsonwebtoken")
        ecosystem: Ecosystem (e.g., "npm", "pip", "go", "maven")
        version: Specific version to check (optional; if provided, only vulns affecting this version)

    Returns:
        {"vulnerabilities": [{id, summary, affected, ...}, ...]} from OSV API
        Returns {"vulnerabilities": []} if no results.
    """
    payload = {
        "package": {"name": package, "ecosystem": ecosystem},
    }
    if version:
        payload["version"] = version

    try:
        resp = requests.post(
            f"{BASE_URL}/query",
            json=payload,
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "vulnerabilities": []}


def resolve_ghsa_id(ghsa_id: str) -> dict[str, Any]:
    """Look up a vulnerability directly by its own ID (GHSA-*, real OSV IDs,
    etc.) via OSV's real GET /v1/vulns/{id} endpoint - the correct real
    lookup for "I already have an ID, give me its record", as opposed to
    /v1/query which resolves a package+ecosystem+version to whatever IDs
    affect it (see query_osv above).

    Returns the vulnerability record, or {} if not found.
    """
    try:
        resp = requests.get(f"{BASE_URL}/vulns/{ghsa_id}", timeout=TIMEOUT)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}
