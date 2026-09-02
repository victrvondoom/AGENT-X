"""Real Nutrient DWS (Document Web Services) client - the DevNetwork
Nutrient Challenge integration: real Data Extraction on security documents
and real digital signing of the sealed Evidence Report.

Endpoints and auth verified against Nutrient's own public developer pages
(https://www.nutrient.io/api/) as of this build:
  - base URL:        https://api.nutrient.io
  - auth:            Authorization: Bearer <NUTRIENT_API_KEY>
  - data extraction: POST /build
  - digital signing: POST /sign
Sign up for a free API key at https://dashboard.nutrient.io/sign_up/ and
set NUTRIENT_API_KEY - every function below raises a clear error until
that's set, rather than silently returning a fabricated result.
"""

from __future__ import annotations

import hashlib
import json
import os

import requests

# Importing config for its load_dotenv() side effect, so this module sees
# NUTRIENT_API_KEY no matter how it's entered - the standalone preflight
# (`python -m app.integrations.nutrient_dws`) doesn't go through the server,
# and without this it reads a bare os.environ that .env was never loaded into.
from app import config as _config  # noqa: F401

BASE_URL = "https://api.nutrient.io"


class NutrientDWSError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("NUTRIENT_API_KEY")
    if not key:
        raise NutrientDWSError(
            "NUTRIENT_API_KEY is not set. Sign up for a free key at "
            "https://dashboard.nutrient.io/sign_up/ and set it as an env var."
        )
    return key


def extract_document(file_path: str, schema: dict | None = None) -> dict:
    """Real call to POST /build - extracts structured, auditable data from a
    document (e.g. a CVE advisory PDF, a vendor security disclosure). Pass a
    JSON Schema in `schema` to constrain extraction to specific fields with
    per-field citations back to the source, matching a security-report shape
    (advisory_id, severity, affected_versions, remediation, etc.)."""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    with open(file_path, "rb") as f:
        files = {"file": f}
        data = {}
        if schema is not None:
            data["extract_schema"] = json.dumps(schema)
        resp = requests.post(f"{BASE_URL}/build", headers=headers, files=files, data=data, timeout=60)
    if resp.status_code != 200:
        raise NutrientDWSError(f"Nutrient DWS /build error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def sign_evidence_report(pdf_path: str, signed_out_path: str, sign_data: dict | None = None) -> dict:
    """Real call to POST /sign - applies a certificate-based digital
    signature to a rendered Evidence Report PDF, making it tamper-evident.

    Per Nutrient's API reference this endpoint returns the *signed PDF as a
    binary stream*, not JSON. Treating the response as JSON is a real
    mistake: resp.json() raises on PDF bytes, which would make DWS sealing
    fail for every record even with a perfectly valid API key.

    Returns metadata about the signed artifact, including its SHA-256. That
    digest is what gets recorded as the seal reference - it identifies the
    exact signed document that was issued, and anyone holding the PDF can
    recompute it.
    """
    headers = {"Authorization": f"Bearer {_api_key()}"}
    with open(pdf_path, "rb") as f:
        # `data` must be present, and must carry an application/json content
        # type - omitting it returns 400 with failingPaths $.data. An empty
        # object is valid and produces an invisible signature, which is what
        # we want here: the Evidence Report's page count varies per finding,
        # so a fixed visible-signature rect could land on top of content.
        resp = requests.post(
            f"{BASE_URL}/sign",
            headers=headers,
            files={
                "file": (os.path.basename(pdf_path), f, "application/pdf"),
                "data": (None, json.dumps(sign_data or {}), "application/json"),
            },
            timeout=120,
        )
    if resp.status_code != 200:
        raise NutrientDWSError(f"Nutrient DWS /sign error {resp.status_code}: {resp.text[:500]}")

    signed_bytes = resp.content
    if not signed_bytes.startswith(b"%PDF"):
        # Fail loudly rather than storing a "seal" over something that
        # isn't a signed PDF at all.
        raise NutrientDWSError(
            f"Nutrient DWS /sign returned {len(signed_bytes)} bytes that are not a PDF "
            f"(starts with {signed_bytes[:16]!r})"
        )

    with open(signed_out_path, "wb") as f:
        f.write(signed_bytes)

    return {
        "signed_pdf_path": signed_out_path,
        "sha256": hashlib.sha256(signed_bytes).hexdigest(),
        "bytes": len(signed_bytes),
    }


def is_configured() -> bool:
    return bool(os.environ.get("NUTRIENT_API_KEY"))


def html_to_pdf(html: str, out_path: str) -> str:
    """Real call to POST /build - renders an HTML document to PDF. This is
    the first half of sealing an Evidence Report: DWS can only digitally
    sign a document, and the Evidence Agent's native output is JSON, so the
    record is rendered to a real PDF here before /sign is called on it."""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    instructions = {"parts": [{"html": "index.html"}]}
    files = {
        "index.html": ("index.html", html.encode("utf-8"), "text/html"),
    }
    data = {"instructions": json.dumps(instructions)}
    resp = requests.post(f"{BASE_URL}/build", headers=headers, files=files, data=data, timeout=90)
    if resp.status_code != 200:
        raise NutrientDWSError(f"Nutrient DWS /build error {resp.status_code}: {resp.text[:500]}")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path


def seal_evidence_document(html: str, pdf_out_path: str, signed_out_path: str | None = None) -> dict:
    """Full real DWS round trip for one Evidence Report: render the record
    to PDF via /build, then digitally seal that PDF via /sign.

    Raises NutrientDWSError if NUTRIENT_API_KEY isn't set or either call
    fails - it never degrades to a fabricated seal, because a seal that
    wasn't actually issued by DWS would make the Evidence Report claim
    something untrue.
    """
    signed_out_path = signed_out_path or pdf_out_path.replace(".pdf", ".signed.pdf")
    html_to_pdf(html, pdf_out_path)
    signed = sign_evidence_report(pdf_out_path, signed_out_path)
    return {"pdf_path": pdf_out_path, **signed}


def preflight() -> dict:
    """Validates a configured NUTRIENT_API_KEY by doing a real, minimal
    round trip: render a one-line HTML doc to PDF, then sign it. Run with

        python -m app.integrations.nutrient_dws

    so a key can be verified in seconds without waiting for a full
    investigation to reach the Evidence Agent.
    """
    import tempfile

    if not is_configured():
        return {"ok": False, "reason": "NUTRIENT_API_KEY is not set"}
    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "preflight.pdf")
        signed = os.path.join(tmp, "preflight.signed.pdf")
        result = seal_evidence_document(
            "<!doctype html><html><body><h1>SENTINEL DWS preflight</h1></body></html>", pdf, signed
        )
        return {"ok": True, "sha256": result["sha256"], "bytes": result["bytes"]}


if __name__ == "__main__":
    try:
        outcome = preflight()
    except NutrientDWSError as exc:
        print(f"[nutrient-dws] FAILED: {exc}")
        raise SystemExit(1)
    if not outcome["ok"]:
        print(f"[nutrient-dws] not configured: {outcome['reason']}")
        print("Get a free key at https://dashboard.nutrient.io/sign_up/ then set NUTRIENT_API_KEY.")
        raise SystemExit(2)
    print(f"[nutrient-dws] OK - built and signed a PDF ({outcome['bytes']} bytes)")
    print(f"[nutrient-dws] signed document sha256: {outcome['sha256']}")
