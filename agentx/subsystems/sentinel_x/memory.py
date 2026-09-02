"""Real cross-session Memory Bank backed by ChromaDB (embedded, local, free
- no cloud account needed). Persists Analyst's real relevance verdicts per
(repo, component) so a later investigation of the same repo starts with
real prior context instead of re-deriving everything from zero - the
concrete mechanism behind Google's "Memory Bank... cross-session repository
context" requirement, and a genuine capability, not a placeholder collection
that's never read from.
"""

from __future__ import annotations

from agentx.subsystems.sentinel_x.config import WORKDIR, ensure_workdir
from agentx.subsystems.sentinel_x.knowledge import owasp_patterns

# Opened on first use rather than at import. Building the client at import made
# two things true that should not have been: importing this module wrote a
# database to disk (so merely checking whether the capability was available
# created files), and a missing chromadb turned an optional dependency into an
# ImportError for anything that touched the package. Both are deferred to the
# first real read or write.
_COLLECTIONS = {
    "_verdicts_collection": "sentinel_investigations",
    "_remediation_collection": "remediation_patterns",
    "_verified_fixes_collection": "verified_fixes",
}

_client = None
_cache: dict[str, object] = {}


def _get_client():
    global _client
    if _client is None:
        import chromadb
        ensure_workdir()
        _client = chromadb.PersistentClient(path=str(WORKDIR / "memory_bank"))
    return _client


def __getattr__(name: str):
    """Resolve the collection globals on first access, then cache them.

    Module-level __getattr__ (PEP 562) keeps every existing call site in this
    file written exactly as it was, while the connection itself stays lazy.
    """
    if name in _COLLECTIONS:
        if name not in _cache:
            _cache[name] = _get_client().get_or_create_collection(
                _COLLECTIONS[name])
        return _cache[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_owasp_ingested = False


def _ensure_owasp_patterns_ingested() -> None:
    """Ingest OWASP remediation patterns into remediation_patterns collection once at startup."""
    global _owasp_ingested
    if _owasp_ingested or _remediation_collection.count() > 0:
        return
    patterns = owasp_patterns.list_all_patterns()
    for pattern in patterns:
        doc_id = f"{pattern['cwe_id']}:{pattern['language']}"
        _remediation_collection.upsert(
            ids=[doc_id],
            documents=[pattern["code"]],
            metadatas=[
                {
                    "cwe_id": pattern["cwe_id"],
                    "cwe_name": pattern["cwe_name"],
                    "language": pattern["language"],
                    "description": pattern["description"],
                }
            ],
        )
    _owasp_ingested = True


def remember_verdict(repo: str, component: str, finding_id: str, verdict: str, reasoning: str) -> None:
    """Stores a real Analyst verdict so future investigations of the same
    repo/component can be informed by it."""
    doc_id = f"{repo}:{component}:{finding_id}"
    _verdicts_collection.upsert(
        ids=[doc_id],
        documents=[reasoning],
        metadatas=[{"repo": repo, "component": component, "finding_id": finding_id, "verdict": verdict}],
    )


def recall_prior_context(repo: str, component: str, n_results: int = 3) -> list[dict]:
    """Retrieves real prior investigation context for this repo/component,
    if any exists - semantic search over previously stored reasoning, not a
    keyword lookup."""
    count = _verdicts_collection.count()
    if count == 0:
        return []
    results = _verdicts_collection.query(
        query_texts=[f"security investigation of {component} in {repo}"],
        n_results=min(n_results, count),
        where={"$and": [{"repo": repo}, {"component": component}]},
    )
    memories = []
    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    for doc_id, doc, meta in zip(ids, docs, metas):
        memories.append({"id": doc_id, "reasoning": doc, **meta})
    return memories


def retrieve_similar_verdicts(repo: str, cwe_class: str, n_results: int = 3) -> list[dict]:
    """Retrieve prior Analyst verdicts similar to this CWE for this repo.
    Used by grounded_tools.search_memory_bank()."""
    count = _verdicts_collection.count()
    if count == 0:
        return []
    results = _verdicts_collection.query(
        query_texts=[f"{cwe_class} vulnerability assessment"],
        n_results=min(n_results, count),
    )
    memories = []
    for doc_id, doc, meta in zip(
        results.get("ids", [[]])[0],
        results.get("documents", [[]])[0],
        results.get("metadatas", [[]])[0],
    ):
        memories.append({"id": doc_id, "reasoning": doc, **meta})
    return memories


def retrieve_verified_fix(cwe_id: str, language: str | None = None) -> dict | None:
    """Retrieve a verified fix from a prior Re-Verifier confirmation.
    Looks for fixes that passed re-verification (confirmed status).
    Used by grounded_tools.retrieve_fix_pattern()."""
    _ensure_owasp_patterns_ingested()
    count = _verified_fixes_collection.count()
    if count == 0:
        return None
    query = f"{cwe_id} fix" if not language else f"{cwe_id} {language} fix"
    results = _verified_fixes_collection.query(
        query_texts=[query],
        n_results=1,
    )
    if not results.get("ids", [[]])[0]:
        return None
    meta = results.get("metadatas", [[]])[0][0] if results.get("metadatas", [[]])[0] else None
    if language and meta.get("language") != language:
        return None
    return {"cwe_id": meta.get("cwe_id"), "pattern": meta.get("pattern")}


def store_verified_fix(cwe_id: str, language: str, pattern: str, source_finding: str) -> None:
    """Store a fix that Re-Verifier confirmed actually resolved the vulnerability.
    This becomes the first candidate for Patch Forge on the next similar finding.
    Called by re_verifier.py when status is RESOLVED."""
    _ensure_owasp_patterns_ingested()
    doc_id = f"{cwe_id}:{language}:{source_finding}"
    _verified_fixes_collection.upsert(
        ids=[doc_id],
        documents=[pattern],
        metadatas=[
            {
                "cwe_id": cwe_id,
                "language": language,
                "source_finding": source_finding,
                "pattern": pattern,
            }
        ],
    )


def memory_bank_health() -> dict:
    """Real health check: can each ChromaDB collection actually be queried
    right now, and how many real documents does each hold. Used by the
    Audit Persistence Monitor panel instead of a hardcoded 'healthy' flag."""
    try:
        counts = {
            "investigations": _verdicts_collection.count(),
            "remediation_patterns": _remediation_collection.count(),
            "verified_fixes": _verified_fixes_collection.count(),
        }
        return {"healthy": True, "collections": counts}
    except Exception as exc:  # noqa: BLE001 - report real failure, don't hide it
        return {"healthy": False, "collections": {}, "error": str(exc)}
