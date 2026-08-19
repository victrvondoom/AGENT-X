"""
Agent X MCP server.

Lets any MCP-compatible AI agent (Claude Code, Claude Desktop, Cursor, …) use Agent X's
verifiable memory as tools: remember facts, recall them (grounded + honest), provably forget a
subject with proof, and inspect the memory. Backed by CockroachDB — the same engine the web app uses.

Run:      python mcp_server.py
Connect:  claude mcp add agent-x -- python /ABS/PATH/agent-x/mcp_server.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP  # noqa: E402

from core.ingest import ingest_document          # noqa: E402
from core.ask import ask as _ask                  # noqa: E402
from core.forget import forget as _forget, prior_state, verify_gone  # noqa: E402
from db import store                              # noqa: E402

mcp = FastMCP("agent-x")


@mcp.tool
def remember(subject: str, text: str, workspace: str = "default") -> dict:
    """Store a memory about `subject` (a person, system, or entity) in the given workspace.
    Extracts entities and relationships into the knowledge graph. Returns a small receipt."""
    try:
        return ingest_document(subject, subject, text, workspace)
    except store.KeyDestroyed:
        return {"error": f"'{subject}' was permanently erased; the name cannot be re-onboarded"}


@mcp.tool
def recall(query: str, workspace: str = "default") -> str:
    """Answer a question strictly from stored memory. If the answer isn't on record, says so —
    it never hallucinates. This honesty is what makes Agent X's forgetting provable."""
    return _ask(query, None, workspace)[0]


@mcp.tool
def forget(subject: str, workspace: str = "default") -> dict:
    """Verifiably erase `subject` from memory: one atomic CockroachDB transaction deletes its
    documents, graph nodes, edges, and vectors, and crypto-shreds its key. Returns a 3-part proof
    (it existed via AS OF SYSTEM TIME · it's gone · it's irreversible)."""
    r = _forget(subject, workspace)
    return {
        "receipt": r,
        "proof_prior_existence": prior_state(subject, r["t_before"], workspace),
        "proof_of_absence": verify_gone(subject, workspace),
    }


@mcp.tool
def list_subjects(workspace: str = "default") -> list:
    """List the subjects currently held in memory for a workspace."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute("SELECT DISTINCT subject FROM documents WHERE workspace = %s ORDER BY subject",
                  (workspace,))
        return [r[0] for r in c.fetchall()]


@mcp.tool
def memory_timeline(workspace: str = "default") -> list:
    """Recent memory events (ingest / forget / demote / restore) for a workspace, newest first."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT kind, subject, detail, created_at::string FROM timeline "
            "WHERE workspace = %s ORDER BY created_at DESC LIMIT 50",
            (workspace,),
        )
        return [{"kind": r[0], "subject": r[1], "detail": r[2], "at": r[3]} for r in c.fetchall()]


if __name__ == "__main__":
    mcp.run()
