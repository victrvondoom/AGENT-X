"""NVD client — NIST's official CVE database. Free; request API key at
https://nvd.nist.gov/developers/request-an-api-key for higher rate limits.
"""

from __future__ import annotations

import os
import requests
from typing import Any

BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
TIMEOUT = 30

API_KEY = os.environ.get("NVD_API_KEY")
HEADERS = {}
if API_KEY:
    HEADERS["X-API-Key"] = API_KEY


def query_nvd_by_cve_id(cve_id: str) -> dict[str, Any]:
    """Look up a CVE by ID (e.g., 'CVE-2025-1234').

    Returns:
        {"vulnerabilities": [{...cvss, descriptions, etc...}]} or empty if not found.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}?keywordSearch={cve_id}",
            headers=HEADERS,
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "vulnerabilities": []}


def query_nvd_by_keyword(keyword: str, limit: int = 5) -> dict[str, Any]:
    """Search NVD by keyword (e.g., 'jsonwebtoken algorithm confusion').

    Returns top `limit` results.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}?keywordSearch={keyword}&resultsPerPage={limit}",
            headers=HEADERS,
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "vulnerabilities": []}


def extract_cvss_from_nvd(nvd_record: dict) -> float | None:
    """Extract CVSS v3 score from NVD record if present."""
    vulns = nvd_record.get("vulnerabilities", [])
    if not vulns:
        return None
    cve = vulns[0]
    metrics = cve.get("cveMetadata", {}).get("metrics", {})
    cvss_v3 = metrics.get("cvssV3", {})
    return cvss_v3.get("baseScore")
