"""Evidence signing and tamper-detection tests.

SENTINEL's pitch is "we don't ask you to trust the agent, we ask the agent
to produce evidence." That only means something if a record whose content
changed after sealing actually fails verification. These tests hold that
line: they mutate a sealed record field by field and assert the signature
stops matching.
"""

from __future__ import annotations

import copy

import pytest

from app.agents.evidence_agent import _sign, render_report_html, verify_signature


def _sealed_record() -> dict:
    payload = {
        "finding_id": "SENTINEL-F-GHSA-8cf7-32gw-wr33",
        "repo": "juice-shop/juice-shop",
        "commit": "419a8d4f9bb5d98d2854b9b22c4bb40200405ad3",
        "timeline": [{"actor": "Hunter", "action": "Detected GHSA-8cf7-32gw-wr33", "ts": "2026-01-01T00:00:00Z"}],
        "final_status": "RESOLVED",
        "verdict": {"finding_id": "f", "verdict": "confirmed", "reasoning": "reachable", "claims": []},
        "verification_results": [
            {
                "finding_id": "f",
                "scenario": "RS256->HS256 forgery",
                "expected": "rejected",
                "observed": "rejected",
                "result": "RESOLVED",
                "sandbox_id": "sb-1",
                "duration_ms": 1234,
            }
        ],
        "patch_proposal": {
            "finding_id": "f",
            "branch_name": "sentinel/fix-x",
            "files_changed": ["lib/insecurity.ts"],
            "diff": "- old\n+ new",
            "generated_test_paths": ["test/a.spec.ts"],
            "explanation": "restrict algorithms",
        },
    }
    return {**payload, "signature": _sign(payload)}


def test_untampered_record_verifies():
    assert verify_signature(_sealed_record()) is True


def test_signature_is_deterministic_and_content_addressed():
    a, b = _sealed_record(), _sealed_record()
    assert a["signature"] == b["signature"]
    assert a["signature"].startswith("sha256:")


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.__setitem__("final_status", "CONFIRMED_EXPLOITABLE"), id="flip-final-status"),
        pytest.param(lambda r: r.__setitem__("commit", "0" * 40), id="swap-commit"),
        pytest.param(lambda r: r.__setitem__("repo", "attacker/repo"), id="swap-repo"),
        pytest.param(lambda r: r["timeline"].append({"actor": "Ghost", "action": "fabricated", "ts": "x"}), id="append-timeline-entry"),
        pytest.param(lambda r: r["timeline"].clear(), id="erase-timeline"),
        pytest.param(lambda r: r["verdict"].__setitem__("verdict", "not_relevant"), id="downgrade-verdict"),
        pytest.param(lambda r: r["verification_results"][0].__setitem__("result", "RESOLVED" if False else "INCONCLUSIVE"), id="alter-verification-result"),
        pytest.param(lambda r: r["verification_results"].clear(), id="erase-verification-results"),
        pytest.param(lambda r: r["patch_proposal"].__setitem__("diff", "- old\n+ malicious"), id="swap-patch-diff"),
        pytest.param(lambda r: r["patch_proposal"].__setitem__("branch_name", "attacker/branch"), id="swap-branch"),
    ],
)
def test_any_content_change_breaks_the_seal(mutate):
    """Every field that a reader would rely on must be covered by the
    signature - a tampered record has to fail, not merely look different."""
    record = _sealed_record()
    mutate(record)
    assert verify_signature(record) is False


def test_missing_signature_does_not_pass():
    record = _sealed_record()
    record["signature"] = None
    assert verify_signature(record) is False


def test_report_html_escapes_agent_output():
    """Agent output is untrusted text that ends up in a rendered document -
    it must not be able to inject markup into the Evidence Report."""
    payload = {
        "finding_id": "f",
        "repo": "r",
        "final_status": "RESOLVED",
        "timeline": [{"actor": "<script>alert(1)</script>", "action": "<img onerror=x>", "ts": "t"}],
        "verdict": {"verdict": "confirmed", "reasoning": "<b>bold</b>"},
        "verification_results": [],
        "patch_proposal": {"branch_name": "b", "files_changed": [], "diff": "</pre><script>bad()</script>"},
    }
    html = render_report_html(payload, "sha256:abc")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror=x" not in html or "&lt;img onerror=x&gt;" in html
