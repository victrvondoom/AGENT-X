# What is this?

**In one line:** Agent X is agent memory you can *erase* — completely, in one
atomic transaction — and then *prove* it's gone.

Every agentic-memory project this cycle answers the same question: *how do agents
remember more?* Agent X answers the one that regulated, production memory actually
demands: **when a memory is wrong, poisoned, or legally required to disappear — can
you delete it everywhere, atomically, and prove it?**

*The whole loop, over one store:*

```mermaid
flowchart LR
  R["remember<br/>store a fact"] --> S[("one CockroachDB store<br/>graph plus vectors plus audit")]
  S --> C["recall<br/>answer from memory"]
  C --> F["forget<br/>erase a subject"]
  F --> P["certificate<br/>proof it is gone"]
  F -.-> S
  class R,C v
  class S s
  class F bad
  class P s3
  classDef v fill:#6d28d9,stroke:#a78bfa,color:#fff
  classDef s fill:#4c1d95,stroke:#c4b5fd,color:#fff
  classDef bad fill:#9d174d,stroke:#f472b6,color:#fff
  classDef s3 fill:#92400e,stroke:#fbbf24,color:#fff
```

## ELI10

Imagine an assistant that has read every runbook your team ever wrote. One night
something breaks and it tells you exactly what to check — calm, in plain words.

Now the clever part. Last month you decommissioned the old cache. A *dumb* assistant
still says "check the old cache!" — because it remembers everything forever, including
junk. Agent X is smarter: when something is retired, it can be **forgotten** — the
documents, the graph entities, the vectors, and the encryption key — so it never sends
you chasing a thing that no longer exists. And it hands you a **certificate** proving
the forget happened.

Most people build memory that *remembers more*. Agent X is built to **forget the
right things, on command, and prove it.**

## Why "forgetting" is the hard half

Deletion in most systems is a best-effort `DELETE` that leaves recoverable vectors on
disk, orphaned graph edges, and no proof anything happened. Agent X makes deletion a
**first-class, verifiable operation** — which turns out to be exactly what a database
built for correctness (CockroachDB) is uniquely good at.
