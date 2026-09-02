"""Real async worker: polls the job queue (LocalQueue by default; PubSub or
EventBridge once cloud credentials are configured - see app/queue) and runs
the full real six-stage investigation for each "investigate_finding" job.

This is the literal mechanism behind "a person can trigger an investigation,
close their laptop, and come back to a completed, evidenced result" - run
this as a separate long-lived process from whatever enqueues jobs (an API
route, a CLI command, a scheduled trigger); it doesn't need the enqueuing
process to still be running.

Usage:
    ./.venv/Scripts/python.exe -m app.worker            # run forever, polling
    ./.venv/Scripts/python.exe -m app.worker --once      # process one job and exit
"""

from __future__ import annotations

import sys
import time

from agentx.subsystems.sentinel_x.agent_tools import (
    analyst_assess_relevance,
    evidence_agent_seal_record,
    patch_forge_generate_patch,
    re_verifier_confirm_fix,
)
from agentx.subsystems.sentinel_x import orchestrator
from agentx.subsystems.sentinel_x.queue import Job, get_queue
from agentx.subsystems.sentinel_x.queue.base import JobQueue


def run_investigation(finding_id: str) -> dict:
    """Runs the real six-stage loop, stages 2-6 (Hunter's scan already ran to
    produce finding_id in the first place), through whichever orchestrator
    SENTINEL_ORCHESTRATOR selects - see app/orchestrator.py. All three
    options execute the exact same underlying agent functions; only who
    decides the call order differs."""
    choice = orchestrator.active_orchestrator()
    if choice == "adk":
        return orchestrator.run_via_adk(finding_id)
    if choice == "strands":
        return orchestrator.run_via_strands(finding_id)
    return run_investigation_direct(finding_id)


def run_investigation_direct(finding_id: str) -> dict:
    """The deterministic default: call each real agent function in order and
    thread each stage's real output into the next, ending with Evidence Agent
    sealing the actual verdict/patch/re-verification results - not just a
    bare "detected" entry. No orchestration LLM is involved in choosing the
    order, so this path is reproducible and costs nothing beyond the agents'
    own Gemini calls."""
    verdict = analyst_assess_relevance(finding_id)
    patch = patch_forge_generate_patch(finding_id)
    reverify_result = re_verifier_confirm_fix(finding_id, patch["branch_name"], patch_proposal=patch)
    evidence = evidence_agent_seal_record(
        finding_id,
        verdict=verdict,
        verification_results=reverify_result["results"],
        patch_proposal=reverify_result["final_patch_proposal"],
    )
    return {
        "verdict": verdict,
        "patch": patch,
        "reverify": reverify_result,
        "evidence": evidence,
    }


def process_job(queue: JobQueue, job: Job) -> None:
    try:
        if job.job_type == "investigate_finding":
            result = run_investigation(job.payload["finding_id"])
        else:
            raise ValueError(f"Unknown job_type: {job.job_type}")
        queue.complete(job.job_id, result)
        print(f"[worker] job {job.job_id} ({job.job_type}) -> done")
    except Exception as exc:  # noqa: BLE001 - a failed job should never crash the worker loop
        queue.fail(job.job_id, str(exc))
        print(f"[worker] job {job.job_id} ({job.job_type}) -> FAILED: {exc}")


def main() -> None:
    once = "--once" in sys.argv
    queue = get_queue()
    print(f"[worker] started, backend={type(queue).__name__}")
    while True:
        job = queue.claim_next()
        if job is None:
            if once:
                print("[worker] no queued jobs.")
                return
            time.sleep(2)
            continue
        print(f"[worker] claimed job {job.job_id} ({job.job_type}): {job.payload}")
        process_job(queue, job)
        if once:
            return


if __name__ == "__main__":
    main()
