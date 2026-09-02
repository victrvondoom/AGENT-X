"""EPSS client — exploit-probability scoring from first.org. Free, no auth.
Useful for Analyst's severity reasoning beyond raw CVSS.
"""

from __future__ import annotations

import requests
from typing import Any

BASE_URL = "https://api.first.org/data/v1/epss"
TIMEOUT = 30


def query_epss_by_cve(cve_id: str) -> dict[str, Any]:
    """Look up EPSS probability for a CVE ID.

    Args:
        cve_id: CVE ID (e.g., 'CVE-2025-1234')

    Returns:
        {"data": [{cve, epss, percentile, date}]} or empty if not found.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}?filter=cve:{cve_id}",
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "data": []}


def get_epss_by_date(cve_id: str, date: str | None = None) -> dict[str, Any]:
    """Get EPSS data for a CVE, optionally at a specific date (YYYY-MM-DD format).

    Returns the first matching record.
    """
    try:
        params = {"filter": f"cve:{cve_id}"}
        if date:
            params["filter"] += f":date={date}"
        resp = requests.get(
            BASE_URL,
            params=params,
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "data": []}
