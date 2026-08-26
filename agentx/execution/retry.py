"""
Exponential backoff with jitter, and the policy that decides whether a failed
provider call is worth attempting again.

TWO KINDS OF "AGAIN", NOT ONE

A dropped TCP connection and a rate limit that clears in an hour are both
"retryable", but they do not belong to the same mechanism. `runner.run()` blocks
the request that called it, so this module only ever authorises SHORT,
in-process retries (a handful of attempts, each under a couple of seconds) for
the case where trying again immediately is likely to just work. Anything the
computed or provider-supplied delay would make longer than that is left alone —
the execution record captures why and when, and the EXISTING scheduled
mechanisms (`sentinel.py`'s `retry_execution` remediation, the follow-up clock)
pick it up later, the same way they already own every other kind of waiting in
this system. There is deliberately no long-sleep retry loop anywhere here.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .providers.base import ProviderResult, ErrorCode, RETRYABLE_CODES

# Codes an in-process retry must never fire for, regardless of `retryable`: each
# one needs something only a person (or a later, deliberate action) can supply —
# retrying immediately would just fail the same way, immediately, again.
NEVER_RETRY = (ErrorCode.AUTH_REQUIRED, ErrorCode.PERMISSION_DENIED,
              ErrorCode.INVALID_INPUT, ErrorCode.EXTERNAL_REJECTED,
              ErrorCode.NON_RETRYABLE, ErrorCode.CONFLICT, ErrorCode.REQUIRES_USER)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3          # total attempts, including the first — not extra retries
    base_delay: float = 0.4        # seconds
    max_delay: float = 2.0         # seconds — see module docstring on why this stays short
    jitter: float = 0.3            # fraction of the computed delay, randomised either way
    max_inline_delay: float = 2.0  # a computed/provider delay above this is NOT waited out
                                    # in-process; the caller records it and stops instead


DEFAULT_POLICY = RetryPolicy()


def should_retry(result: ProviderResult, attempt: int, policy: RetryPolicy = DEFAULT_POLICY) -> bool:
    """Whether a NEW attempt is warranted, given what attempt `attempt` returned.

    `attempt` is 1-indexed and counts the attempt just made, so `attempt=1`
    failing with `max_attempts=3` still permits two more.
    """
    if result.ok:
        return False
    if attempt >= policy.max_attempts:
        return False
    if result.error_code in NEVER_RETRY:
        return False
    if result.error_code:
        return result.error_code in RETRYABLE_CODES
    return bool(result.retryable)   # old-style result with no error_code set


def delay_for(attempt: int, policy: RetryPolicy = DEFAULT_POLICY, *,
             retry_after: float | None = None) -> float:
    """Seconds to wait before making attempt number `attempt + 1`.

    A provider-supplied `retry_after` is authoritative when present — the
    provider knows its own rate limit better than a generic formula does — even
    when that means the delay exceeds what an in-process retry will actually
    wait out (`should_wait_inline` is what decides that, separately).
    """
    if retry_after is not None:
        return max(0.0, float(retry_after))
    base = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    spread = base * policy.jitter
    return max(0.0, base + random.uniform(-spread, spread))


def should_wait_inline(delay: float, policy: RetryPolicy = DEFAULT_POLICY) -> bool:
    """Whether `delay` is short enough to block on inside one `run()` call."""
    return delay <= policy.max_inline_delay


def call_with_retry(fn, *, policy: RetryPolicy = DEFAULT_POLICY,
                    sleep=time.sleep) -> tuple[ProviderResult, int, list[dict]]:
    """Call `fn()` (a zero-arg thunk returning ProviderResult), retrying inline
    while `should_retry` says so and the wait is short enough to block on.

    Returns (final_result, attempts_made, attempt_log) — the log is what
    `runner.run()` folds into the stored execution record, so a case that
    needed three tries before an email went out has that written down rather
    than looking, from the outside, identical to one that succeeded first try.
    `sleep` is injectable so tests can assert on backoff timing without a real
    process actually pausing for it.
    """
    attempt = 0
    log: list[dict] = []
    while True:
        attempt += 1
        result = fn()
        log.append({"attempt": attempt, "ok": result.ok, "outcome": result.outcome,
                    "error_code": result.error_code, "message": result.message})
        if not should_retry(result, attempt, policy):
            return result, attempt, log
        delay = delay_for(attempt, policy, retry_after=result.retry_after)
        if not should_wait_inline(delay, policy):
            log[-1]["deferred_to_scheduler"] = True
            log[-1]["retry_after"] = delay
            return result, attempt, log
        log[-1]["retried_after_seconds"] = round(delay, 3)
        sleep(delay)
