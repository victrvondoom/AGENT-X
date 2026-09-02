"""Thin wrapper around the real Gemini API (generativelanguage.googleapis.com).
Every call here is a genuine network request - no templated/canned text
anywhere. Used by Analyst (relevance reasoning) and Patch Forge (patch
generation).
"""

from __future__ import annotations

import json
import random
import time

import requests

from app.config import GEMINI_API_KEY, GEMINI_MODEL

# Re-exported so callers and traces can report the model actually used.
MODEL = GEMINI_MODEL
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


class LLMError(RuntimeError):
    pass


# 429 = rate limited, 5xx = Gemini overloaded or briefly unavailable.
# All are transient; none of them mean the request itself was wrong.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter.

    The jitter matters here: the six agents in a run can hit the same
    overloaded model at once, and identical backoff makes them retry in
    lockstep and collide again on every attempt.
    """
    return min(2**attempt * 5, 60) * (0.7 + random.random() * 0.6)


def _explain_failure(resp) -> str:
    """Turn an API failure into something the operator can act on.

    Quota exhaustion and transient overload both arrive as 429/503 and read
    almost identically, but the responses are opposite: one needs a higher
    tier or a wait for the window to reset, the other just needs another
    attempt. During a timed demo, "Gemini API error 429" alone sends people
    hunting through logs for a bug that is not in this codebase.
    """
    body = resp.text[:1000]
    lowered = body.lower()
    if resp.status_code == 429 and "quota" in lowered:
        return (
            f"Gemini quota exhausted for model '{MODEL}'. This is an account limit, not a "
            "fault in the fleet - retrying will not clear it. Either wait for the quota "
            "window to reset, enable billing on the API key, or set GEMINI_MODEL to another "
            f"Gemini 3.5+ model with headroom. Raw response: {body}"
        )
    if resp.status_code in RETRYABLE_STATUS:
        return (
            f"Gemini is temporarily unavailable for '{MODEL}' (HTTP {resp.status_code}) after "
            f"retrying. This is upstream capacity, not a fault in the fleet. Raw: {body}"
        )
    return f"Gemini API error {resp.status_code}: {body}"


def call_gemini(
    prompt: str, response_schema: dict | None = None, temperature: float = 0.2, timeout: int = 150
) -> str:
    """Makes one real call to Gemini. If response_schema is given, asks the
    model to return JSON conforming to that schema and returns the raw JSON
    text (still a string - callers parse it with json.loads)."""
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set - export it before calling any LLM-backed agent.")

    generation_config: dict = {"temperature": temperature}
    if response_schema is not None:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = response_schema

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    max_retries = 5
    resp = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                API_URL,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            # A dropped connection mid-investigation is transient in exactly
            # the same way a 503 is, and just as fatal if we give up on it.
            if attempt == max_retries - 1:
                raise LLMError(f"Gemini API unreachable after {max_retries} attempts: {exc}") from exc
            time.sleep(_backoff(attempt))
            continue

        if resp.status_code not in RETRYABLE_STATUS:
            break
        if attempt == max_retries - 1:
            break
        # 429 is real free-tier rate limiting; 5xx is Gemini itself being
        # overloaded or briefly unavailable. Both are transient, and both
        # used to end an investigation that had already done 5 minutes of
        # real work - a clone, an npm audit, a sandboxed exploit run - which
        # is a terrible way to lose a demo. Back off and try again.
        time.sleep(_backoff(attempt))

    assert resp is not None
    if resp.status_code != 200:
        raise LLMError(_explain_failure(resp))

    data = resp.json()
    try:
        candidates = data["candidates"]
        parts = candidates[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Unexpected Gemini response shape: {json.dumps(data)[:1000]}") from exc

    if not text.strip():
        finish_reason = data.get("candidates", [{}])[0].get("finishReason", "unknown")
        raise LLMError(f"Gemini returned empty text (finishReason={finish_reason})")

    return text
