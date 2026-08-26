"""
The retry engine — exponential backoff with jitter, and the boundary between a
short in-process retry and a wait long enough that only the scheduler should
own it.
"""
from __future__ import annotations

from agentx.execution import retry as R
from agentx.execution.providers.base import ErrorCode, ProviderResult


def _ok():
    return ProviderResult(True, "accepted", "test:p", "sandbox")


def _fail(code=None, retryable=False, retry_after=None):
    return ProviderResult(False, "error", "test:p", "sandbox", message="failed",
                          error_code=code, retryable=retryable, retry_after=retry_after)


class TestShouldRetry:
    def test_a_successful_result_is_never_retried(self):
        assert R.should_retry(_ok(), attempt=1) is False

    def test_retryable_code_is_retried_below_max_attempts(self):
        policy = R.RetryPolicy(max_attempts=3)
        assert R.should_retry(_fail(ErrorCode.RETRYABLE), attempt=1, policy=policy) is True
        assert R.should_retry(_fail(ErrorCode.RETRYABLE), attempt=2, policy=policy) is True

    def test_retryable_code_stops_at_max_attempts(self):
        policy = R.RetryPolicy(max_attempts=3)
        assert R.should_retry(_fail(ErrorCode.RETRYABLE), attempt=3, policy=policy) is False

    def test_timeout_and_rate_limited_are_retryable(self):
        assert R.should_retry(_fail(ErrorCode.TIMEOUT), attempt=1) is True
        assert R.should_retry(_fail(ErrorCode.RATE_LIMITED), attempt=1) is True

    def test_auth_required_is_never_retried_even_if_retryable_flag_is_set(self):
        """A stale/incorrect `retryable=True` on an AUTH_REQUIRED result must not
        override the error_code — asking again immediately just fails again."""
        assert R.should_retry(_fail(ErrorCode.AUTH_REQUIRED, retryable=True), attempt=1) is False

    def test_permission_denied_invalid_input_and_external_rejected_never_retry(self):
        for code in (ErrorCode.PERMISSION_DENIED, ErrorCode.INVALID_INPUT,
                    ErrorCode.EXTERNAL_REJECTED, ErrorCode.NON_RETRYABLE,
                    ErrorCode.CONFLICT, ErrorCode.REQUIRES_USER):
            assert R.should_retry(_fail(code), attempt=1) is False, code

    def test_old_style_result_with_no_error_code_falls_back_to_retryable_flag(self):
        assert R.should_retry(_fail(None, retryable=True), attempt=1) is True
        assert R.should_retry(_fail(None, retryable=False), attempt=1) is False


class TestDelayFor:
    def test_delay_grows_with_attempt_number(self):
        policy = R.RetryPolicy(base_delay=1.0, max_delay=100.0, jitter=0.0)
        assert R.delay_for(1, policy) == 1.0
        assert R.delay_for(2, policy) == 2.0
        assert R.delay_for(3, policy) == 4.0

    def test_delay_is_capped_at_max_delay(self):
        policy = R.RetryPolicy(base_delay=1.0, max_delay=3.0, jitter=0.0)
        assert R.delay_for(10, policy) == 3.0

    def test_jitter_stays_within_bounds(self):
        policy = R.RetryPolicy(base_delay=10.0, max_delay=10.0, jitter=0.5)
        for _ in range(50):
            d = R.delay_for(1, policy)
            assert 5.0 <= d <= 15.0

    def test_provider_supplied_retry_after_overrides_the_backoff_formula(self):
        policy = R.RetryPolicy(base_delay=0.1, max_delay=1.0)
        assert R.delay_for(1, policy, retry_after=42.0) == 42.0

    def test_delay_is_never_negative(self):
        policy = R.RetryPolicy(base_delay=0.01, max_delay=0.01, jitter=5.0)
        for _ in range(50):
            assert R.delay_for(1, policy) >= 0.0


class TestShouldWaitInline:
    def test_short_delay_is_waited_inline(self):
        policy = R.RetryPolicy(max_inline_delay=2.0)
        assert R.should_wait_inline(1.0, policy) is True

    def test_long_delay_is_deferred(self):
        policy = R.RetryPolicy(max_inline_delay=2.0)
        assert R.should_wait_inline(60.0, policy) is False


class TestCallWithRetry:
    def test_a_first_try_success_makes_exactly_one_call(self):
        calls = []
        def fn():
            calls.append(1)
            return _ok()
        result, attempts, log = R.call_with_retry(fn, sleep=lambda s: None)
        assert result.ok and attempts == 1 and len(calls) == 1
        assert log[0]["attempt"] == 1 and "retried_after_seconds" not in log[0]

    def test_retries_a_transient_failure_until_it_succeeds(self):
        outcomes = [_fail(ErrorCode.TIMEOUT), _fail(ErrorCode.TIMEOUT), _ok()]
        def fn():
            return outcomes.pop(0)
        slept = []
        result, attempts, log = R.call_with_retry(
            fn, policy=R.RetryPolicy(max_attempts=5), sleep=slept.append)
        assert result.ok and attempts == 3
        assert len(slept) == 2, "one sleep between each of the two failed attempts"
        assert all(s >= 0 for s in slept)

    def test_stops_at_max_attempts_and_returns_the_last_failure(self):
        def fn():
            return _fail(ErrorCode.TIMEOUT)
        result, attempts, log = R.call_with_retry(
            fn, policy=R.RetryPolicy(max_attempts=3), sleep=lambda s: None)
        assert not result.ok and attempts == 3
        assert len(log) == 3

    def test_never_retries_a_non_retryable_failure(self):
        calls = []
        def fn():
            calls.append(1)
            return _fail(ErrorCode.PERMISSION_DENIED)
        result, attempts, log = R.call_with_retry(fn, sleep=lambda s: None)
        assert attempts == 1 and len(calls) == 1

    def test_a_long_retry_after_is_deferred_rather_than_slept_out(self):
        """A provider saying 'try again in 5 minutes' must not block the request
        that called run() for 5 minutes — the scheduler owns waits that long."""
        def fn():
            return _fail(ErrorCode.RATE_LIMITED, retry_after=300.0)
        slept = []
        result, attempts, log = R.call_with_retry(
            fn, policy=R.RetryPolicy(max_attempts=5), sleep=slept.append)
        assert attempts == 1, "must not have looped waiting out a 5-minute delay"
        assert not slept
        assert log[0]["deferred_to_scheduler"] is True
        assert log[0]["retry_after"] == 300.0
