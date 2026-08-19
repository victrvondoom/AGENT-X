# Agent X over MCP — two servers, one governed store

Agent X is reachable through the Model Context Protocol two complementary ways. Both are backed by
the **same CockroachDB cluster**, so what an agent sees is always the real, live memory — never a copy.

| MCP server | What it is | What it's for |
|------------|-----------|---------------|
| **Agent X MCP server** (`mcp_server.py`, FastMCP) | Our own high-level memory tools | `remember`, `recall` (grounded + honest), `forget` (atomic + 3-part proof), `list_subjects`, `memory_timeline` |
| **CockroachDB Cloud MCP Server** (managed, hosted by Cockroach Labs) | Cockroach Labs' own managed MCP endpoint over the cluster | Raw, **independent** SQL inspection of the store — `select_query`, `list_tables`, `get_table_schema`, `explain_query` |

Both are wired in [`.mcp.json`](../.mcp.json) at the repo root.

**Cross-platform:** the managed `cockroachdb-cloud` server is a hosted HTTP endpoint — no local runtime, identical on Windows / Linux / macOS. The local `agent-x` server launches via `uv run mcp_server.py`, so it works the same on every OS (no `.venv/bin` vs `.venv\Scripts` path differences). One-time setup: install [uv](https://docs.astral.sh/uv/), then `uv venv && uv pip install -r requirements.txt`.

## Why the managed server matters — verifiable erasure you don't have to take our word for

Agent X's whole thesis is *provable* forgetting. The weakest link in any "we deleted it" claim is that
the proof comes from **the same system that did the deleting**. A judge — or an auditor, or a regulator —
has every reason to distrust it.

The **CockroachDB Cloud Managed MCP Server** closes that gap. It is Cockroach Labs' own hosted endpoint,
completely outside Agent X's code. Point *any* MCP agent at it and run SQL directly against the cluster:

1. **Before** a forget — confirm the subject exists:
   ```sql
   SELECT count(*) FROM nodes  WHERE 'payments-service' = ANY(subjects);
   SELECT count(*) FROM documents WHERE subject = 'payments-service';
   ```
2. Run **Forget & Prove** in the app (or the `forget` tool).
3. **After** — the same independent query returns **0**, and the subject's key row is shredded:
   ```sql
   SELECT count(*) FROM nodes  WHERE 'payments-service' = ANY(subjects);   -- 0
   SELECT destroyed_at IS NOT NULL AS key_shredded FROM subject_keys
     WHERE subject = 'payments-service';                                   -- true
   SELECT * FROM erasure_events WHERE subject_sha256 IS NOT NULL ORDER BY created_at DESC LIMIT 1;
   ```

The proof-of-absence now comes from **CockroachDB itself**, through Cockroach Labs' managed endpoint —
not from an Agent X API call anyone could accuse of lying. That is the strongest form the claim can take.

The managed server also blocks destructive ops (`DROP`/`TRUNCATE`) and system schemas, so handing it to an
external auditor is safe: they can *verify*, but they can't tamper.

## Connect the managed server (one-time)

The managed endpoint is hosted — there is no cluster-side toggle. You authorize a client and point it at the cluster:

1. **CockroachDB Cloud Console → your `agent-x` cluster → Connect →** the MCP option. Copy the generated
   snippet (authoritative field names live here, in case the console updates them).
2. It's already scaffolded in [`.mcp.json`](../.mcp.json):
   ```json
   {
     "mcpServers": {
       "cockroachdb-cloud": {
         "type": "http",
         "url": "https://cockroachlabs.cloud/mcp",
         "headers": { "mcp-cluster-id": "0c59b7a3-0638-498b-bb54-0b45d75b6615" }
       }
     }
   }
   ```
3. **Auth** — two options:
   - **Interactive (OAuth 2.1 + PKCE)** — the default in the file. On first use the MCP client opens a browser
     login to your CockroachDB Cloud account (scopes `mcp:read` / `mcp:write`). Nothing secret is stored. Best
     for the demo.
   - **Autonomous (service-account API key)** — create a service account in the Cloud Console, grant it a
     cluster-scoped role, and add its key as a bearer token:
     ```json
     "headers": {
       "mcp-cluster-id": "0c59b7a3-0638-498b-bb54-0b45d75b6615",
       "Authorization": "Bearer ${COCKROACH_MCP_API_KEY}"
     }
     ```
     Keep the key in `.env` (git-ignored), never in the committed file.

Then, in an MCP client (Claude Code / Claude Desktop / Cursor) that has this repo's `.mcp.json` loaded, ask:
*"Using cockroachdb-cloud, list the tables, then select count(*) from nodes."* — the agent talks straight to
your cluster through Cockroach Labs' managed endpoint.

## Reference

- Managed server: https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server
- MCP server tool reference: https://www.cockroachlabs.com/docs/v26.2/cockroachdb-mcp-server
- CockroachDB + AI overview: https://www.cockroachlabs.com/docs/stable/cockroachdb-and-ai
