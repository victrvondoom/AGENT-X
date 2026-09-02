"""Knowledge-grounding tests.

The grounding gate exists so an unresolvable advisory ID can never be
presented as a confirmed finding, and so Patch Forge never invents a fix
for a vulnerability class it has no reference pattern for. Both are
offline-safe: they use the real code paths but never touch the network.
"""

from __future__ import annotations

import pytest

from app.knowledge import owasp_patterns
from app.schemas import Finding, Severity


# --- OWASP remediation pattern library -----------------------------------


def test_known_cwe_returns_a_pattern():
    assert owasp_patterns.get_pattern_for_language("CWE-327", "javascript") is not None


def test_cwe_lookup_is_case_insensitive():
    """Patch Forge lowercases the CWE id it reads off a finding; the
    library is keyed uppercase. A case mismatch here silently escalated
    every finding to human review."""
    assert owasp_patterns.get_pattern_by_cwe("cwe-327") is not None
    assert owasp_patterns.get_pattern_by_cwe("CWE-327") is not None
    assert owasp_patterns.get_pattern_for_language("cwe-327", "javascript") is not None


def test_unknown_cwe_returns_nothing_rather_than_a_guess():
    assert owasp_patterns.get_pattern_by_cwe("CWE-99999") is None
    assert owasp_patterns.get_pattern_for_language("CWE-99999", "javascript") is None


def test_unknown_language_for_known_cwe_returns_nothing():
    assert owasp_patterns.get_pattern_for_language("CWE-327", "brainfuck") is None


def test_the_flagship_demo_finding_has_a_pattern():
    """GHSA-8cf7-32gw-wr33 is classified CWE-327. Without a pattern for it
    Patch Forge refuses to generate a fix, which broke the demo once."""
    pattern = owasp_patterns.get_pattern_for_language("CWE-327", "javascript")
    assert pattern is not None
    assert "algorithms" in pattern


def test_every_catalogued_pattern_is_well_formed():
    for entry in owasp_patterns.list_all_patterns():
        assert entry["cwe_id"].startswith("CWE-")
        assert entry["language"]
        assert entry["code"].strip()


# --- Hunter's grounding gate ---------------------------------------------


def _finding(advisory_id: str | None) -> Finding:
    return Finding(
        finding_id=f"SENTINEL-F-{advisory_id or 'NONE'}",
        severity=Severity.critical,
        component="jsonwebtoken",
        version="<=8.5.1",
        source="npm audit",
        advisory_id=advisory_id,
    )


def test_grounding_gate_drops_findings_with_no_advisory_id(monkeypatch):
    """A finding with no resolvable identifier must not reach Analyst as
    though it were confirmed."""
    from app.agents import hunter

    monkeypatch.setattr(
        hunter, "lookup_vulnerability", lambda _id: {"resolved": False, "source": None, "record": None}
    )
    assert hunter._apply_grounding_gate([_finding(None)]) == []


def test_grounding_gate_drops_unresolvable_advisory_ids(monkeypatch):
    from app.agents import hunter

    monkeypatch.setattr(
        hunter, "lookup_vulnerability", lambda _id: {"resolved": False, "source": None, "record": None}
    )
    assert hunter._apply_grounding_gate([_finding("CVE-9999-FAKE")]) == []


def test_grounding_gate_keeps_resolved_findings_and_records_the_source(monkeypatch):
    from app.agents import hunter

    monkeypatch.setattr(
        hunter,
        "lookup_vulnerability",
        lambda _id: {"resolved": True, "source": "osv", "record": {"id": _id, "summary": "real"}},
    )
    kept = hunter._apply_grounding_gate([_finding("GHSA-8cf7-32gw-wr33")])
    assert len(kept) == 1
    assert kept[0].grounding_source == "osv"
    assert kept[0].verified_advisory_record["summary"] == "real"


def test_grounding_gate_does_not_fail_open_when_lookup_errors(monkeypatch):
    """If the knowledge source is unreachable the finding must be dropped,
    not waved through - degrading silently to ungrounded isn't grounding."""
    from app.agents import hunter

    monkeypatch.setattr(
        hunter,
        "lookup_vulnerability",
        lambda _id: {"resolved": False, "source": None, "record": None, "error": "network down"},
    )
    assert hunter._apply_grounding_gate([_finding("GHSA-8cf7-32gw-wr33")]) == []


# --- degraded-scan handling ----------------------------------------------


def test_gate_distinguishes_unreachable_from_genuinely_unresolved(monkeypatch):
    """These must not both present as "no findings": one is a real result,
    the other means we could not check."""
    from app.agents import hunter

    monkeypatch.setattr(
        hunter, "lookup_vulnerability",
        lambda _id: {"resolved": False, "source": None, "record": None, "error": "network down"},
    )
    hunter._apply_grounding_gate([_finding("GHSA-a"), _finding("GHSA-b")])
    assert hunter.last_scan.errored == 2
    assert hunter.last_scan.degraded is True

    monkeypatch.setattr(
        hunter, "lookup_vulnerability",
        lambda _id: {"resolved": False, "source": None, "record": None},
    )
    hunter._apply_grounding_gate([_finding("GHSA-a")])
    assert hunter.last_scan.errored == 0
    assert hunter.last_scan.degraded is False


def test_a_degraded_scan_does_not_overwrite_good_cached_findings(monkeypatch):
    """A transient DNS blip during a scan previously pinned an empty result
    for the process lifetime and blanked the entire dashboard."""
    from app.agents import hunter
    from app import server

    good = _finding("GHSA-real")
    good.grounding_source = "osv"

    monkeypatch.setattr(server, "_findings_cache", [good.model_dump(mode="json")])
    # Both branches of the exists() check call server.hunt, which is patched.
    monkeypatch.setattr(server, "hunt", lambda *a, **k: [])
    monkeypatch.setattr(hunter, "last_scan", hunter.ScanStats(raw=25, grounded=0, errored=25))

    result = server._load_findings(force=True)
    assert len(result) == 1, "a degraded scan must not blank out known-good findings"
