"""
Seed a rich demo knowledge base into Agent X (default workspace).

An SRE / incident-response memory: detailed runbooks + post-mortems whose text names many
entities (systems, teams, metrics, failure modes, incidents) so the extracted graph is DENSE
and cross-referenced. Shared entities (the api-gateway edge, the core-platform team, Redis)
mean a Forget & Prove on one subject demonstrates shared-node *retention*.

Review dates are backdated so Curation (aging records, memory-health, the decay cycle) and the
Timeline have real content. Run:  python scripts/seed_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ingest import ingest_document   # noqa: E402
from db import store                       # noqa: E402

WS = "default"

# (subject, title, runbook text, reviewed_days_ago)
CORPUS = [
    ("api-gateway", "api-gateway runbook",
     "Runbook: api-gateway. The api-gateway is the public north-south entrypoint, owned by the "
     "core-platform team. It terminates TLS, authenticates every request against auth-service, and "
     "applies per-client quotas through the rate-limiter at the edge. Key metrics: p99 latency, 5xx "
     "rate, and 429 rate. On a 429 spike, check the rate-limiter client-quota configuration before "
     "scaling replicas. All traffic to payments-service, search-index and the cdn passes through the "
     "gateway, so a gateway outage is a platform-wide incident.", 12),
    ("auth-service", "auth-service runbook",
     "Runbook: auth-service. auth-service issues and validates session tokens for the api-gateway and "
     "is owned by the core-platform team. It stores active sessions in session-store (a Redis cluster) "
     "and reads from the primary session-store. High login latency almost always means session-store "
     "connection-pool saturation — check the bounded pool size first. A failed auth-service deploy "
     "surfaces as a 401 storm at the api-gateway. Escalation: the core-platform on-call.", 118),
    ("payments-service", "payments-service runbook",
     "Runbook: payments-service. payments-service handles charges, refunds and settlement, owned by the "
     "payments team. It reads and writes payments-db and caches idempotency keys in redis-cache. During "
     "the nightly settlement window, long-running queries against payments-db can exhaust the connection "
     "pool; check the primary before failing over to a read replica. payments-service calls "
     "notification-service to send receipt emails, and depends on the api-gateway for inbound webhooks.", 76),
    ("payments-db", "payments-db runbook",
     "Runbook: payments-db. payments-db is the primary transactional store for payments-service, owned by "
     "the payments team. Watch for long-running queries and ledger-table lock contention during nightly "
     "settlement. Read replicas serve the reporting workload so the primary stays free for "
     "payments-service writes. A replication-lag alert on payments-db usually precedes a settlement delay.", 154),
    ("redis-cache", "redis-cache runbook",
     "Runbook: redis-cache. redis-cache is a shared Redis cache used by payments-service for idempotency "
     "keys and by the api-gateway for hot config. Owned by the core-platform team. A cache-miss storm "
     "after a flush can stampede payments-db, so warm the cache before peak traffic. redis-cache replaced "
     "the deprecated legacy-cache. Watch the hit-rate metric and eviction count.", 40),
    ("session-store", "session-store runbook",
     "Runbook: session-store. session-store is the Redis cluster backing auth-service sessions, owned by "
     "the core-platform team. The bounded connection-pool size is the usual root cause of login latency. "
     "It shares the Redis operational playbook with redis-cache. The primary session-store handles writes; "
     "replicas serve read-heavy token validation from the api-gateway.", 83),
    ("search-index", "search-index runbook",
     "Runbook: search-index. search-index powers product search behind the api-gateway, owned by the "
     "discovery team. Reindex jobs run off the payments-db read replica for catalog data. Stale shards "
     "cause missing results; trigger a shard rebuild and verify personalization against the primary "
     "session-store. Watch query latency and the reindex-lag metric. A full reindex is a discovery-team "
     "change with a maintenance window.", 372),
    ("notification-service", "notification-service runbook",
     "Runbook: notification-service. notification-service sends transactional email and push, owned by the "
     "growth team. payments-service calls it for receipts and search-index for saved-search alerts. It "
     "relays outbound email through cloud-mailer and tracks the bounce-rate metric; a rising bounce rate "
     "usually means cloud-mailer throttling or a sender-reputation problem. Depends on the api-gateway for "
     "inbound webhook callbacks.", 187),
    ("cloud-mailer", "cloud-mailer runbook",
     "Runbook: cloud-mailer. cloud-mailer is the outbound email relay used by notification-service, owned "
     "by the growth team. On throttling, drain the outbound spool slowly and check the sender reputation "
     "before retrying so the bounce-rate does not climb. cloud-mailer integrates with a third-party SMTP "
     "provider and is rate-limited per domain.", 103),
    ("cdn", "cdn runbook",
     "Runbook: cdn. The cdn fronts static assets and image-resizer output, owned by the core-platform team. "
     "Purge the cdn on every release; a stale edge cache serves old product images. The origin for signed "
     "asset URLs is the api-gateway. Watch cache-hit ratio and origin-fetch latency. A cdn purge is a "
     "core-platform change.", 258),
    ("rate-limiter", "rate-limiter runbook",
     "Runbook: rate-limiter. The rate-limiter enforces per-client quotas at the api-gateway edge, owned by "
     "the core-platform team. On a 429 spike, check the client-quota configuration; a misconfigured quota "
     "throttles payments-service webhook callbacks and triggers retries. The rate-limiter reads quota "
     "config from redis-cache and emits a throttle-rate metric.", 45),
    ("image-resizer", "image-resizer runbook",
     "Runbook: image-resizer. image-resizer generates responsive product images served through the cdn, "
     "owned by the discovery team. Long queue depth means the worker pool is undersized; scale the "
     "image-resizer workers and confirm the cdn is caching the output. Watch the queue-depth and "
     "p95-resize-time metrics. A backlog here degrades search-index result thumbnails.", 590),
    ("legacy-cache", "legacy-cache runbook (deprecated)",
     "Runbook: legacy-cache. legacy-cache was the previous caching tier before redis-cache, owned by the "
     "core-platform team. It is deprecated and being decommissioned. When auth-service latency was high, "
     "the old runbook said to flush and resize the legacy-cache cluster — that guidance is now stale, "
     "because redis-cache has taken over. Any runbook still pointing on-call at legacy-cache should be "
     "forgotten once decommissioning completes.", 62),
    # ── incident post-mortems (extra docs → richer graph, cross-linking many systems) ──
    ("legacy-cache", "post-mortem 2024-05 login outage",
     "Post-mortem 2024-05. A legacy-cache memory-eviction storm caused a platform-wide login outage: "
     "auth-service session reads fell through to the database, session-store connection pools saturated, "
     "and the api-gateway returned a 401 storm. Remediation: flushed and resized legacy-cache, then "
     "accelerated the migration to redis-cache. Owning teams: core-platform (auth-service, session-store) "
     "and the payments team (downstream payments-service impact).", 96),
    ("payments-service", "post-mortem 2024-08 settlement delay",
     "Post-mortem 2024-08. During nightly settlement, long-running queries on payments-db held ledger-table "
     "locks, exhausting the payments-service connection pool; receipt emails via notification-service and "
     "cloud-mailer backed up and the bounce-rate spiked. Remediation: moved reporting to a payments-db read "
     "replica and added a settlement query timeout. Owning team: payments.", 30),
    ("api-gateway", "incident 2024-07 429 storm",
     "Incident 2024-07. A misconfigured rate-limiter client-quota caused a 429 storm at the api-gateway; "
     "payments-service webhook callbacks retried and amplified load on redis-cache and payments-db. "
     "Remediation: corrected the client-quota configuration and added a quota-change canary. Owning team: "
     "core-platform.", 20),
]


def main():
    print("Resetting the default workspace…")
    with store.connect() as conn, conn.cursor() as c:
        for t in ("documents", "nodes", "edges", "erasure_events", "timeline"):
            c.execute(f"DELETE FROM {t} WHERE workspace = %s", (WS,))
        c.execute("DELETE FROM subject_keys WHERE workspace = %s", (WS,))

    for subj, title, text, _ in CORPUS:
        r = ingest_document(subj, title, text, WS)
        print(f"  ingested {subj:22s} · {title[:34]:36s} -> {r.get('entities', '?')} entities")

    with store.connect() as conn, conn.cursor() as c:
        for subj, _, _, days in CORPUS:
            c.execute(
                "UPDATE documents SET reviewed_at = now() - (%s || ' days')::INTERVAL "
                "WHERE workspace = %s AND subject = %s",
                (days, WS, subj),
            )
        c.execute("SELECT count(DISTINCT subject), count(*) FROM documents WHERE workspace=%s", (WS,))
        subs, docs = c.fetchone()
        c.execute("SELECT count(*) FROM nodes WHERE workspace=%s", (WS,))
        nodes = c.fetchone()[0]
        c.execute("SELECT count(*) FROM edges WHERE workspace=%s", (WS,))
        edges = c.fetchone()[0]
    print(f"\nDone. {subs} subjects · {docs} documents · {nodes} graph nodes · {edges} relationships in '{WS}'.")


if __name__ == "__main__":
    main()
