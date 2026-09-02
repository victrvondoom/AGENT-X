"""Reclaiming investigations whose worker died mid-run.

A job is only "running" while some process is actually running it. When a
worker is killed - a restart, a crashed Cloud Run instance, a laptop lid -
its claim is never released. The dedup check in start_investigation then
keeps handing that dead job back, and that finding can never be
investigated again for the life of the queue.

Two jobs were found wedged this way in the real queue, stuck "running"
for a day, which is what prompted this.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import server
from app.queue.base import Job


def _job(status: str, minutes_ago: float) -> Job:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    j = Job.new("investigate_finding", {"finding_id": "SENTINEL-F-X"})
    j.status = status
    j.updated_at = ts
    return j


def test_a_long_silent_running_job_is_stale():
    assert server._is_stale(_job("running", server.STALE_JOB_MINUTES + 5)) is True


def test_a_recently_updated_running_job_is_not_stale():
    """The guard must not kill healthy work: a real investigation runs for
    10-15 minutes and must be left alone."""
    assert server._is_stale(_job("running", 12)) is False


def test_a_queued_job_can_also_go_stale():
    assert server._is_stale(_job("queued", server.STALE_JOB_MINUTES + 1)) is True


def test_finished_jobs_are_never_stale():
    """done/failed jobs are terminal - reclaiming them would rewrite history."""
    for status in ("done", "failed"):
        assert server._is_stale(_job(status, 10_000)) is False


def test_an_unparseable_timestamp_is_not_treated_as_stale():
    """Fail safe: if we cannot tell how old a job is, leave it alone rather
    than kill work that may well be running."""
    j = _job("running", 1)
    j.updated_at = "not-a-timestamp"
    assert server._is_stale(j) is False


def test_a_naive_timestamp_is_handled():
    """Older records were written without a timezone; comparing those
    against an aware 'now' raises TypeError and would crash the endpoint."""
    j = _job("running", 0)
    j.updated_at = (datetime.now(timezone.utc) - timedelta(minutes=server.STALE_JOB_MINUTES + 5)).replace(tzinfo=None).isoformat()
    assert server._is_stale(j) is True
