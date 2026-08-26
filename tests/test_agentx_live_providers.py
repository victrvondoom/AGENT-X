"""
The live provider — proof that swapping sandbox for real is a registration
change, not a rewrite.

Nothing here ever touches a real network. `smtplib.SMTP`/`SMTP_SSL` are mocked
throughout: this suite proves the provider constructs the right message and
returns the right `ProviderResult` shape, not that a real mail server accepted
it. Sending an actual test email would require a real recipient this repository
has no authorisation to contact, which is exactly the line the product's own
rules draw.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agentx.execution.providers.live_providers import LiveEmailProvider
from agentx.execution.providers.base import ErrorCode

ENV_KEYS = ("AGENT_X_SMTP_HOST", "AGENT_X_SMTP_PORT", "AGENT_X_SMTP_USER",
            "AGENT_X_SMTP_PASSWORD", "AGENT_X_SMTP_FROM", "AGENT_X_SMTP_TLS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts with none of the SMTP variables set."""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture(autouse=True)
def restore_provider_registry():
    """The provider registry is a process-wide singleton (`agentx.execution.
    providers._registry`/`_by_id`), and several tests in this file deliberately
    call `providers.clear()` + `bootstrap(sandbox=False)` to isolate the live
    provider from the sandbox ones. Left in place, that state leaks into every
    OTHER test file that runs later in the same pytest process and expects the
    default (sandbox-on) registry — which is exactly what happened the first
    time this file shipped without this fixture. Snapshot before, restore after,
    regardless of what the test did to it.
    """
    from agentx.execution import providers as _p
    saved_registry = {k: list(v) for k, v in _p._registry.items()}
    saved_by_id = dict(_p._by_id)
    yield
    _p._registry.clear()
    _p._registry.update(saved_registry)
    _p._by_id.clear()
    _p._by_id.update(saved_by_id)


def _configure(monkeypatch, **overrides):
    defaults = {
        "AGENT_X_SMTP_HOST": "smtp.example.test",
        "AGENT_X_SMTP_PORT": "587",
        "AGENT_X_SMTP_USER": "cases@example.test",
        "AGENT_X_SMTP_PASSWORD": "not-a-real-secret",
        "AGENT_X_SMTP_FROM": "cases@example.test",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


class TestConfiguration:
    def test_unconfigured_by_default(self):
        assert LiveEmailProvider.configured() is False

    def test_configured_when_all_four_are_set(self, monkeypatch):
        _configure(monkeypatch)
        assert LiveEmailProvider.configured() is True

    def test_not_configured_with_one_missing(self, monkeypatch):
        _configure(monkeypatch)
        monkeypatch.delenv("AGENT_X_SMTP_PASSWORD")
        assert LiveEmailProvider.configured() is False

    def test_bootstrap_does_not_register_it_when_unconfigured(self):
        from agentx.execution import providers
        providers.clear()
        providers.bootstrap(sandbox=False)
        assert providers.get("live:smtp") is None

    def test_bootstrap_registers_it_once_configured(self, monkeypatch):
        _configure(monkeypatch)
        from agentx.execution import providers
        providers.clear()
        providers.bootstrap(sandbox=False)
        registered = providers.get("live:smtp")
        assert registered is not None
        assert registered.mode == "live"


class TestSendRefusesUnsafely:
    def test_no_recipient_never_touches_the_network(self, monkeypatch):
        _configure(monkeypatch)
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp, patch("smtplib.SMTP_SSL") as smtp_ssl:
            result = p.do_email({"subject": "test", "body": "test"})
        assert result.ok is False
        assert result.outcome == "error"
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()

    def test_partial_configuration_refuses_to_send(self, monkeypatch):
        monkeypatch.setenv("AGENT_X_SMTP_HOST", "smtp.example.test")
        # user/password/from left unset
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp:
            result = p.do_email({"to": "someone@example.test", "body": "x"})
        assert result.ok is False
        smtp.assert_not_called()


class TestFailureTaxonomy:
    """Retrying a wrong password sends the same wrong password again — the
    taxonomy exists so runner.py's retry engine can tell that apart from a
    dropped connection, which IS worth trying again."""

    def test_auth_failure_is_not_retryable(self, monkeypatch):
        import smtplib
        _configure(monkeypatch)
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value.login.side_effect = \
                smtplib.SMTPAuthenticationError(535, b"bad credentials")
            result = p.do_email({"to": "merchant@example.test", "body": "x"})
        assert result.ok is False
        assert result.error_code == ErrorCode.AUTH_REQUIRED
        assert result.retryable is False
        assert "reconnect" in result.message.lower()

    def test_connection_failure_is_retryable(self, monkeypatch):
        _configure(monkeypatch)
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.side_effect = ConnectionRefusedError("refused")
            result = p.do_email({"to": "merchant@example.test", "body": "x"})
        assert result.ok is False
        assert result.error_code == ErrorCode.RETRYABLE
        assert result.retryable is True

    def test_timeout_is_classified_distinctly(self, monkeypatch):
        _configure(monkeypatch)
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.side_effect = TimeoutError("timed out")
            result = p.do_email({"to": "merchant@example.test", "body": "x"})
        assert result.error_code == ErrorCode.TIMEOUT
        assert result.retryable is True

    def test_technical_detail_never_appears_in_the_user_facing_dict(self, monkeypatch):
        import smtplib
        _configure(monkeypatch)
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value.login.side_effect = \
                smtplib.SMTPAuthenticationError(535, b"bad credentials")
            result = p.do_email({"to": "merchant@example.test", "body": "x"})
        assert result.technical_detail is not None
        assert "technical_detail" not in result.user_dict()
        assert "technical_detail" in result.as_dict()


class TestSendConstructsTheRightMessage:
    def test_starttls_path_sends_and_returns_live_mode(self, monkeypatch):
        _configure(monkeypatch)  # AGENT_X_SMTP_TLS unset -> STARTTLS path
        p = LiveEmailProvider()
        mock_conn = MagicMock()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock_conn
            result = p.do_email({"to": "merchant@example.test",
                                 "subject": "Refund request — case PX-00001",
                                 "body": "Please refund 50.00 GBP."})

        assert result.ok is True
        assert result.mode == "live"
        assert result.provider == "live:smtp"
        assert result.outcome == "accepted"
        assert result.external_ref  # a Message-ID was generated
        assert result.evidence_text is not None
        assert "merchant@example.test" in result.evidence_text
        assert "50.00 GBP" in result.evidence_text
        mock_conn.starttls.assert_called_once()
        mock_conn.login.assert_called_once_with("cases@example.test",
                                                 "not-a-real-secret")
        mock_conn.send_message.assert_called_once()
        sent_msg = mock_conn.send_message.call_args[0][0]
        assert sent_msg["To"] == "merchant@example.test"
        assert sent_msg["From"] == "cases@example.test"

    def test_implicit_tls_path_used_when_tls_flag_is_zero(self, monkeypatch):
        _configure(monkeypatch, AGENT_X_SMTP_TLS="0", AGENT_X_SMTP_PORT="465")
        p = LiveEmailProvider()
        mock_conn = MagicMock()
        with patch("smtplib.SMTP_SSL") as smtp_ssl_cls, \
             patch("smtplib.SMTP") as smtp_cls:
            smtp_ssl_cls.return_value.__enter__.return_value = mock_conn
            result = p.do_email({"to": "x@example.test", "body": "y"})

        assert result.ok is True
        smtp_cls.assert_not_called()
        mock_conn.login.assert_called_once()
        mock_conn.starttls.assert_not_called()

    def test_smtp_failure_is_a_result_not_an_exception(self, monkeypatch):
        _configure(monkeypatch)
        p = LiveEmailProvider()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.side_effect = OSError("connection refused")
            result = p.do_email({"to": "x@example.test", "body": "y"})
        assert result.ok is False
        assert result.outcome == "error"
        assert result.retryable is True
        assert "connection refused" in result.message

    def test_responds_in_days_is_none_not_invented(self, monkeypatch):
        """A live provider must not fabricate a response-time number the way the
        sandbox's stated SLA can — nobody told it one."""
        _configure(monkeypatch)
        p = LiveEmailProvider()
        mock_conn = MagicMock()
        with patch("smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = mock_conn
            result = p.do_email({"to": "x@example.test", "body": "y"})
        assert result.responds_in_days is None


class TestVerificationIsHonestlyAbsent:
    def test_provider_does_not_support_verify(self):
        p = LiveEmailProvider()
        assert p.supports("verify") is False

    def test_runner_reports_unverifiable_for_a_live_email_execution(self, tmp_path, monkeypatch):
        _configure(monkeypatch)
        from agentx import store, chain
        from agentx import case as case_mod
        from agentx.execution import providers, runner

        store.reset_for_tests(str(tmp_path / "live.db"))
        providers.clear()
        providers.bootstrap(sandbox=False)

        with store.connect() as conn:
            c = case_mod.create(conn, description="test", autonomy_level=4)
            c = case_mod.update(conn, c["id"], confidence=0.9)
            assert c is not None
            mock_conn = MagicMock()
            with patch("smtplib.SMTP") as smtp_cls:
                smtp_cls.return_value.__enter__.return_value = mock_conn
                rec = runner.run(conn, case=c, action="email",
                                 params={"to": "merchant@example.test",
                                         "subject": "s", "body": "b",
                                         "case_id": c["id"]},
                                 capability=None)
            assert rec["state"] == "COMPLETED"
            v = runner.verify(conn, case=c, execution_id=rec["id"])
            assert v["verified"] == "unverifiable"
