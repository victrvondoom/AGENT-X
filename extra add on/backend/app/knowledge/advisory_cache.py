"""Disk-backed cache for resolved advisory records.

Resolving 25 findings against OSV/GHSA/NVD costs ~0.85s each, so a cold
scan spent ~21s of wall clock sitting in serial network waits. Advisory
records are effectively immutable once published - GHSA-8cf7-32gw-wr33
describes the same vulnerability today as it did last week - so re-fetching
them on every scan is pure latency for no freshness benefit.

One deliberate rule: **only successful resolutions are cached.** Caching a
failure would let a transient DNS blip persist as "unresolved" long after
the network recovered, which is exactly the failure mode the grounding
gate's degraded-scan detection exists to catch. A miss is always re-tried
against the live source; a hit is always a record we genuinely retrieved.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.config import WORKDIR

CACHE_PATH = WORKDIR / "advisory_cache.json"

# Advisories are near-immutable, but not formally frozen - severity ratings
# and affected ranges are occasionally corrected. A week keeps scans fast
# while still picking up amendments.
TTL_SECONDS = int(os.environ.get("SENTINEL_ADVISORY_CACHE_TTL", 7 * 24 * 3600))

_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] | None = None


def _load() -> dict[str, dict[str, Any]]:
    global _memory
    if _memory is not None:
        return _memory
    try:
        _memory = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        # A corrupt cache is a performance problem, never a correctness one:
        # discard it and re-resolve from the live sources.
        _memory = {}
    return _memory


def get(advisory_id: str) -> dict[str, Any] | None:
    """Return a cached resolution, or None on miss/expiry."""
    with _lock:
        entry = _load().get(advisory_id)
        if entry is None:
            return None
        if time.time() - entry.get("cached_at", 0) > TTL_SECONDS:
            return None
        return entry.get("result")


def put(advisory_id: str, result: dict[str, Any]) -> None:
    """Persist a *successful* resolution. Callers must not pass failures."""
    if not result.get("resolved"):
        return
    with _lock:
        cache = _load()
        cache[advisory_id] = {"cached_at": time.time(), "result": result}
        _flush_locked(cache)


def _flush_locked(cache: dict) -> None:
    """Atomic replace so a crash mid-write cannot leave a torn JSON file
    that every subsequent scan then has to discard."""
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass  # cache is an optimisation; never fail a scan over it


def clear() -> None:
    global _memory
    with _lock:
        _memory = {}
        CACHE_PATH.unlink(missing_ok=True)


def stats() -> dict[str, Any]:
    with _lock:
        return {"entries": len(_load()), "path": str(CACHE_PATH), "ttl_seconds": TTL_SECONDS}
