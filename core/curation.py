"""
Curation — keep memory current between hard erasures (workspace-scoped).

  * demote / restore  — reversible down-weighting of a subject's nodes (soft-forget).
  * stale_references  — after an entity is erased, find surviving nodes/edges that still mention it.
  * aging_documents   — records not reviewed in a while (row-level TTL is the automatic backstop).
"""
from __future__ import annotations

from db import store

NEUTRAL_WEIGHT = 0.5
DEMOTE_MILD = 0.25
DEMOTE_DEEP = 0.05


def demote(subject: str, weight: float = DEMOTE_DEEP, workspace: str = "default") -> int:
    """Reversibly down-weight a subject's nodes so they stop surfacing (record retained)."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "UPDATE nodes SET weight = %s WHERE workspace = %s AND %s::STRING = ANY(subjects) "
            "AND deleted_at IS NULL",
            (weight, workspace, subject),
        )
        n = c.rowcount
        c.execute(
            "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'demote', %s, %s)",
            (workspace, subject, f"demoted {subject} to weight {weight} ({n} nodes) — reversible"),
        )
    return n


def restore(subject: str, workspace: str = "default") -> int:
    """Undo a demote: back to neutral weight."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "UPDATE nodes SET weight = %s WHERE workspace = %s AND %s::STRING = ANY(subjects) "
            "AND deleted_at IS NULL",
            (NEUTRAL_WEIGHT, workspace, subject),
        )
        n = c.rowcount
        c.execute(
            "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'restore', %s, %s)",
            (workspace, subject, f"restored {subject} to neutral weight ({n} nodes)"),
        )
    return n


def stale_references(subject: str | None = None, workspace: str = "default") -> list[dict]:
    """Surviving nodes/edges whose text still mentions an already-erased subject."""
    with store.connect() as conn, conn.cursor() as c:
        if subject:
            erased = [subject]
        else:
            c.execute("SELECT DISTINCT subject FROM erasure_events WHERE workspace = %s", (workspace,))
            erased = [r[0] for r in c.fetchall()]
        hits: list[dict] = []
        for subj in erased:
            like = f"%{subj}%"
            c.execute(
                "SELECT name, description FROM nodes "
                "WHERE workspace = %s AND deleted_at IS NULL AND description ILIKE %s",
                (workspace, like),
            )
            hits += [{"kind": "node", "where": r[0], "mentions": subj, "text": r[1]} for r in c.fetchall()]
            c.execute(
                "SELECT relationship, description FROM edges WHERE workspace = %s AND description ILIKE %s",
                (workspace, like),
            )
            hits += [{"kind": "edge", "where": r[0], "mentions": subj, "text": r[1]} for r in c.fetchall()]
        return hits


STALE_DAYS = 180        # aging → eligible for reversible auto-demote
VERY_STALE_DAYS = 365   # very stale → queued for your approval before permanent delete


def run_cycle(apply: bool = False, workspace: str = "default") -> dict:
    """The decay loop: one bounded pass over every record by review-age. Aging knowledge is
    auto-demoted (reversible — it sinks in answers but stays restorable); the very stale are
    queued for approval before any permanent delete. With apply=False this is a FREE preview —
    nothing changes until you apply."""
    aging = aging_documents(STALE_DAYS, workspace)
    to_demote, to_queue, seen = [], [], set()
    for d in aging:
        if d["subject"] in seen:
            continue
        seen.add(d["subject"])
        (to_queue if d["age_days"] >= VERY_STALE_DAYS else to_demote).append(d)

    nodes_demoted = 0
    if apply:
        for d in to_demote:
            nodes_demoted += demote(d["subject"], DEMOTE_MILD, workspace)
        with store.connect() as conn, conn.cursor() as c:
            c.execute(
                "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'demote', %s, %s)",
                (workspace, "curation-cycle",
                 f"decay cycle: auto-demoted {len(to_demote)} aging subjects "
                 f"({nodes_demoted} nodes, reversible); {len(to_queue)} very-stale queued for approval"),
            )
    return {
        "applied": apply,
        "demote": [{"subject": d["subject"], "age_days": d["age_days"]} for d in to_demote],
        "queue": [{"subject": d["subject"], "age_days": d["age_days"]} for d in to_queue],
        "nodes_demoted": nodes_demoted,
    }


def aging_documents(days: int = 180, workspace: str = "default") -> list[dict]:
    """Documents not reviewed within `days` — retention-review candidates (row-level TTL backstops)."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT subject, title, reviewed_at::string, (now()::date - reviewed_at::date) AS age_days "
            "FROM documents WHERE workspace = %s AND deleted_at IS NULL "
            "AND reviewed_at < now() - ((%s)::string || ' days')::interval "
            "ORDER BY reviewed_at",
            (workspace, days),
        )
        return [{"subject": r[0], "title": r[1], "reviewed_at": r[2], "age_days": r[3]} for r in c.fetchall()]
