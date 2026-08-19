"""
Ingest: a prose document -> encrypted storage + an LLM-extracted knowledge graph.

For each document we:
  1. store the raw text ENCRYPTED under the subject's data key,
  2. ask the LLM to extract entities and relationships,
  3. embed and UPSERT nodes by name (deterministic coreference dedup via INSERT .. ON CONFLICT),
  4. insert edges,
all inside the one CockroachDB store. Coreference merge (the same entity mentioned in many
documents becomes ONE node) is what makes cross-document answers and blast-radius possible.
"""
from __future__ import annotations

from db import store
from llm import client

EXTRACT_SYSTEM = (
    "You extract a knowledge graph from a document. Return ONLY JSON, no prose, with exactly two "
    'arrays: {"entities": [{"name": str, "type": str, "description": str}], '
    '"relationships": [{"source": str, "target": str, "relationship": str, "description": str}]}\n'
    "Rules:\n"
    "- entity `name` is a short canonical identifier: lowercase, hyphenated, no filler "
    "(e.g. 'auth-service', 'acme-bank', 'premium-checking-account').\n"
    "- Reuse the EXACT same name for the same real-world thing across entities and relationships "
    "(coreference).\n"
    "- `type` is a short category (e.g. 'service', 'bank', 'account', 'person').\n"
    "- `relationship` is a snake_case verb phrase (e.g. 'depends_on', 'owned_by', 'pays_to').\n"
    "- Extract ONLY what the text supports; never invent. Keep each description to one sentence.\n"
    "- `source` and `target` in relationships must be entity names you also listed in `entities`."
)


def ingest_document(subject: str, title: str, text: str, workspace: str = "default",
                    source_kind: str = "user_evidence") -> dict:
    """Ingest one document about `subject` in `workspace`. Returns a small receipt.

    source_kind marks what the material IS, which decides whether erasure may touch it:
      user_evidence  — a person's records. Erased with them.
      authoritative  — a statute or regulator charter. No personal data, so a subject's
                       erasure must leave it standing.
      derived        — produced by the system from the above.
    """
    if source_kind not in ("user_evidence", "authoritative", "derived"):
        raise ValueError(f"unknown source_kind: {source_kind}")
    with store.connect() as conn:
        blob = store.encrypt_for(conn, workspace, subject, text)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (workspace, subject, title, content_enc, source_kind) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (workspace, subject, title, blob, source_kind),
            )
            doc_id = cur.fetchone()[0]

        graph = client.chat_json(EXTRACT_SYSTEM, f"Document about '{subject}':\n\n{text}")
        entities = graph.get("entities", []) or []
        rels = graph.get("relationships", []) or []

        name_to_id: dict[str, str] = {}
        for e in entities:
            name = (e.get("name") or "").strip()
            if not name:
                continue
            desc = (e.get("description") or "").strip()
            emb = store.to_vector(client.embed(f"{name}. {desc}"))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO nodes (workspace, name, type, description, embedding, doc_ids,
                                       subjects, source_kind)
                    VALUES (%s, %s, %s, %s, %s, ARRAY[%s]::UUID[], ARRAY[%s]::STRING[], %s)
                    ON CONFLICT (workspace, name) DO UPDATE SET
                        description = COALESCE(EXCLUDED.description, nodes.description),
                        embedding   = EXCLUDED.embedding,
                        doc_ids     = array_cat(nodes.doc_ids, EXCLUDED.doc_ids),
                        subjects    = array_cat(nodes.subjects, EXCLUDED.subjects),
                        -- Authority is sticky UPWARD. Coreference merges one entity from many
                        -- documents, so a node named in both a statute and a person's file must
                        -- resolve to authoritative: the failure mode of guessing wrong is
                        -- deleting law, which is unrecoverable, versus retaining a node that
                        -- could have gone, which is visible and fixable.
                        source_kind = CASE
                            WHEN nodes.source_kind = 'authoritative'
                              OR EXCLUDED.source_kind = 'authoritative' THEN 'authoritative'
                            ELSE EXCLUDED.source_kind END,
                        deleted_at  = NULL
                    RETURNING id
                    """,
                    (workspace, name, e.get("type"), desc, emb, doc_id, subject, source_kind),
                )
                name_to_id[name.lower()] = cur.fetchone()[0]

        edge_n = 0
        for r in rels:
            s = name_to_id.get((r.get("source") or "").strip().lower())
            t = name_to_id.get((r.get("target") or "").strip().lower())
            if s and t and s != t:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO edges (workspace, source_id, target_id, relationship, description, doc_ids) "
                        "VALUES (%s, %s, %s, %s, %s, ARRAY[%s]::UUID[])",
                        (workspace, s, t, r.get("relationship"), r.get("description"), doc_id),
                    )
                edge_n += 1

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'ingest', %s, %s)",
                (workspace, subject, f"ingested '{title}': {len(entities)} entities, {edge_n} relationships"),
            )
    return {"doc_id": str(doc_id), "subject": subject, "workspace": workspace,
            "entities": len(entities), "relationships": edge_n}
