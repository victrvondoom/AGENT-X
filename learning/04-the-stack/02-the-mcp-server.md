# The MCP server

Agent X ships its own **MCP server** (FastMCP) so any MCP-compatible agent — Claude
Desktop / Code, Cursor — can use verifiable memory as tools, backed by the same
CockroachDB the web app uses.

*An auditor can verify erasure without trusting the app:*

```mermaid
flowchart LR
  AUD["auditor"] --> MCP["CockroachDB Cloud<br/>Managed MCP"]
  MCP --> SEL["SELECT on the cluster<br/>directly"]
  SEL --> DB[("same CockroachDB")]
  SEL --> R["confirms 0 rows<br/>for the subject"]
  class AUD,MCP q
  class SEL v
  class DB s
  class R ok
  classDef q fill:#075985,stroke:#38bdf8,color:#fff
  classDef v fill:#6d28d9,stroke:#a78bfa,color:#fff
  classDef s fill:#4c1d95,stroke:#c4b5fd,color:#fff
  classDef ok fill:#065f46,stroke:#34d399,color:#eafff5
```

```
claude mcp add agent-x -- python /ABS/PATH/agent-x/mcp_server.py
```

Five tools:

| Tool | What it does |
|---|---|
| `remember(subject, text)` | store a memory; extract entities + relationships |
| `recall(query)` | answer strictly from memory; says "not on record" when it doesn't know |
| `forget(subject)` | verifiably erase a subject; returns the 3-part proof |
| `list_subjects()` | what's currently held in this workspace |
| `memory_timeline()` | recent ingest / forget / demote / restore events |

`forget` through the MCP layer returns the same proof bundle as the API —
prior-existence via `AS OF SYSTEM TIME`, live proof-of-absence, and crypto-shred — so an
agent doesn't just delete a memory, it can **prove** it did.
