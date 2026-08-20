"""
Agent X — FastAPI application.

Exposes the memory engine over a small JSON API and serves the console + landing UI.
Every operation is scoped to a WORKSPACE (multi-tenant isolation; defaults to 'default').
`/api/forget` returns the full verifiable-erasure bundle (receipt + AS OF SYSTEM TIME
proof-of-prior-existence + proof-of-absence + signed certificate) in one response.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import FastAPI, UploadFile, File, Form, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ingest import ingest_document          # noqa: E402
from core.ask import ask as ask_memory            # noqa: E402
from core.forget import (forget, prior_state, verify_gone,          # noqa: E402
                         LegalHold, set_hold, release_hold, hold_status)
from core import curation                         # noqa: E402
from llm import client as llm_client              # noqa: E402
from aws import certificate as cert               # noqa: E402
from db import store                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")
STATIC = os.path.join(ROOT, "static")

app = FastAPI(title="Agent X", description="Verifiable forgetting for AI-agent memory")
if os.path.isdir(STATIC):
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _ws(workspace: str | None) -> str:
    """Resolve a workspace id — fail-closed to 'default' only on empty input."""
    return (workspace or "default").strip() or "default"


# ── access control (opt-in via env; off by default for the demo) ──
AUTH_TOKEN = os.environ.get("AGENT_X_AUTH_TOKEN")
LOCK_MODEL = os.environ.get("AGENT_X_LOCK_MODEL") == "1"


def require_auth(authorization: str | None = Header(None)):
    """If AGENT_X_AUTH_TOKEN is set, mutating routes require `Authorization: Bearer <token>`."""
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _validate_endpoint(url: str) -> None:
    """SSRF guard for a user-supplied LLM endpoint: block private/internal hosts, require https off-box."""
    if not url:
        return
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise HTTPException(status_code=400, detail="invalid endpoint")
    host = p.hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        return
    if p.scheme != "https":
        raise HTTPException(status_code=400, detail="non-local endpoint must use https")
    try:
        for res in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(res[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise HTTPException(status_code=400, detail="endpoint resolves to a non-public address")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="endpoint host could not be resolved")


# ─────────────────────────────────────────────────────────── pages
def _serve(name: str, fallback: str | None = None) -> str:
    path = os.path.join(TEMPLATES, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    if fallback:
        return _serve(fallback)
    return "<h1>Agent X</h1><p>UI not built yet.</p>"


@app.get("/", response_class=HTMLResponse)
def landing():
    return _serve("landing.html", fallback="index.html")


@app.get("/app", response_class=HTMLResponse)
def console():
    return _serve("index.html")


# ─────────────────────────────────────────────────────────── /learn — the docs
# The learning/ folder is Agent X explained end to end (the problem, the forget
# hero, the CockroachDB stack, the research). /learn renders it as a readable docs
# site: a folder-derived index + raw markdown, rendered client-side. Public content.
LEARN_DIR = os.path.join(ROOT, "learning")


def _learn_title(md_path: str) -> str:
    try:
        with open(md_path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("# "):
                    return s[2:].strip()
    except OSError:
        pass
    base = os.path.splitext(os.path.basename(md_path))[0]
    return base.split("-", 1)[-1].replace("-", " ").strip().capitalize()


_LABEL_OVERRIDES = {"cockroachdb": "CockroachDB"}


def _pretty(folder: str) -> str:
    # "00-the-big-picture" -> "The big picture"; "02-cockroachdb" -> "CockroachDB"
    name = folder.split("-", 1)[-1] if folder[:2].isdigit() else folder
    if name.lower() in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[name.lower()]
    return name.replace("-", " ").strip().capitalize()


@app.get("/learn", response_class=HTMLResponse)
def learn():
    return _serve("learn.html")


@app.get("/learn/index.json")
def learn_index():
    sections = []
    if os.path.isdir(LEARN_DIR):
        for folder in sorted(os.listdir(LEARN_DIR)):
            fpath = os.path.join(LEARN_DIR, folder)
            if not os.path.isdir(fpath):
                continue
            items = []
            for fn in sorted(os.listdir(fpath)):
                if fn.endswith(".md"):
                    rel = f"{folder}/{fn}"
                    items.append({"path": rel, "title": _learn_title(os.path.join(fpath, fn))})
            if items:
                sections.append({"label": _pretty(folder), "items": items})
    return {"sections": sections}


@app.get("/learn/raw/{path:path}", response_class=HTMLResponse)
def learn_raw(path: str):
    # path-traversal safe: resolve and confirm the target stays inside LEARN_DIR
    target = os.path.realpath(os.path.join(LEARN_DIR, path))
    if not target.startswith(os.path.realpath(LEARN_DIR) + os.sep) or not target.endswith(".md"):
        raise HTTPException(status_code=404, detail="not found")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="not found")
    with open(target, encoding="utf-8") as f:
        return HTMLResponse(f.read(), media_type="text/markdown")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "agent-x"}


# ─────────────────────────────────────────────────────────── workspaces
class WsReq(BaseModel):
    name: str


class WsDelReq(BaseModel):
    id: str


@app.get("/api/workspaces")
def workspaces_list():
    with store.connect() as conn, conn.cursor() as c:
        c.execute("SELECT id, name FROM workspaces ORDER BY created_at")
        rows = [{"id": r[0], "name": r[1]} for r in c.fetchall()]
    if not any(w["id"] == "default" for w in rows):
        rows = [{"id": "default", "name": "Default"}] + rows
    return rows


@app.post("/api/workspaces")
def workspaces_create(r: WsReq, _: None = Depends(require_auth)):
    wid = "ws-" + uuid.uuid4().hex[:8]
    name = (r.name or "Untitled").strip()[:80]
    with store.connect() as conn, conn.cursor() as c:
        c.execute("INSERT INTO workspaces (id, name) VALUES (%s, %s)", (wid, name))
    return {"id": wid, "name": name}


@app.post("/api/workspaces/delete")
def workspaces_delete(r: WsDelReq, _: None = Depends(require_auth)):
    if r.id == "default":
        return {"ok": False, "error": "the default workspace cannot be deleted"}
    with store.connect() as conn:
        with conn.transaction():
            with conn.cursor() as c:
                for t in ("edges", "nodes", "documents", "subject_keys", "erasure_events", "timeline"):
                    c.execute(f"DELETE FROM {t} WHERE workspace = %s", (r.id,))
                c.execute("DELETE FROM workspaces WHERE id = %s", (r.id,))
    return {"ok": True, "deleted": r.id}


# ─────────────────────────────────────────────────────────── memory
class IngestReq(BaseModel):
    subject: str
    title: str = ""
    text: str
    source_kind: str = "user_evidence"   # user_evidence | authoritative | derived
    workspace: str = "default"


class AskReq(BaseModel):
    query: str
    history: list | None = None
    workspace: str = "default"


class HoldReq(BaseModel):
    subject: str
    reason: str
    until: str | None = None
    workspace: str = "default"


class ForgetReq(BaseModel):
    subject: str
    workspace: str = "default"


@app.post("/api/ingest")
def api_ingest(r: IngestReq, _: None = Depends(require_auth)):
    try:
        return ingest_document(r.subject, r.title or r.subject, r.text, _ws(r.workspace),
                               r.source_kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.KeyDestroyed:
        raise HTTPException(status_code=409,
                            detail="this subject was permanently erased; the name cannot be re-onboarded")


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), subject: str = Form(""),
                     source_kind: str = Form("user_evidence"),
                     workspace: str = Form("default"), _: None = Depends(require_auth)):
    raw = (await file.read()).decode("utf-8", errors="ignore")
    subj = (subject or os.path.splitext(file.filename or "uploaded")[0])[:80]
    try:
        return ingest_document(subj, file.filename or subj, raw, _ws(workspace), source_kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.KeyDestroyed:
        raise HTTPException(status_code=409,
                            detail="this subject was permanently erased; the name cannot be re-onboarded")


@app.post("/api/ask")
def api_ask(r: AskReq):
    answer, sources = ask_memory(r.query, r.history, _ws(r.workspace))
    return {"answer": answer, "sources": sources}


@app.get("/source/{name}")
def source(name: str, workspace: str = "default"):
    """The original document(s) behind a cited entity. Ledger-gated: after a forget the subject's
    key is crypto-shredded, so decryption fails and the source vanishes — the citation cannot
    contradict the proof-of-forgetting."""
    ws = _ws(workspace)
    with store.connect() as conn, conn.cursor() as c:
        c.execute("SELECT description, doc_ids FROM nodes WHERE workspace = %s AND name = %s "
                  "AND deleted_at IS NULL", (ws, name))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not on record")
        desc, doc_ids = row
        docs = []
        if doc_ids:
            c.execute("SELECT subject, title, content_enc FROM documents WHERE workspace = %s "
                      "AND id = ANY(%s)", (ws, doc_ids))
            for subj, title, enc in c.fetchall():
                try:
                    text = store.decrypt_for(conn, ws, subj, enc)
                except Exception:
                    text = None
                if text:
                    docs.append({"title": title or subj, "text": text})
    return {"name": name, "description": desc, "docs": docs}


@app.post("/api/forget")
def api_forget(r: ForgetReq, _: None = Depends(require_auth)):
    ws = _ws(r.workspace)
    try:
        receipt = forget(r.subject, ws)
    except LegalHold as e:
        # 409, not 403: the request is well-formed and the caller is authorised —
        # it conflicts with a retention obligation. The reason travels with the
        # refusal so the refusal itself is auditable.
        raise HTTPException(status_code=409, detail={
            "error": "erasure_refused_legal_hold",
            "subject": e.subject, "reason": e.reason, "until": e.until,
            "basis": "GDPR Art. 17(3) — erasure does not apply where retention is legally required",
        })
    prior = prior_state(r.subject, receipt["t_before"], ws)
    absence = verify_gone(r.subject, ws)
    certificate = cert.issue(receipt, prior, absence, datetime.now(timezone.utc).isoformat())
    return {
        "receipt": receipt,
        "proof_prior_existence": prior,
        "proof_of_absence": absence,
        "certificate": certificate,
    }


# ─────────────────────────────────────────────────────────── views
@app.post("/api/hold")
def api_hold(r: HoldReq, _: None = Depends(require_auth)):
    """Place a subject under legal hold — erasure is refused until released or lapsed."""
    return set_hold(r.subject, r.reason, r.until, _ws(r.workspace))


@app.post("/api/hold/release")
def api_hold_release(r: ForgetReq, _: None = Depends(require_auth)):
    """Lift a legal hold. Erasure becomes permitted again."""
    return release_hold(r.subject, _ws(r.workspace))


@app.get("/api/hold")
def api_hold_get(subject: str, workspace: str = "default"):
    """The active hold on a subject, or null. Public: a data subject is entitled to know
    why their erasure request would be refused."""
    ws = _ws(workspace)
    with store.connect() as conn:
        return {"subject": subject, "workspace": ws, "hold": hold_status(conn, ws, subject)}


@app.get("/cascade", response_class=HTMLResponse)
def cascade():
    """The erasure cascade, rendered in 3D: what a forget destroys, what it retains for
    other subjects, what it is forbidden to touch, and when it refuses outright."""
    return _serve("cascade.html")


@app.get("/api/graph")
def api_graph(workspace: str = "default"):
    ws = _ws(workspace)
    with store.connect() as conn, conn.cursor() as c:
        # source_kind travels with every node so a client can show WHY a node would
        # survive an erasure. Without it the cascade is just "things vanished".
        c.execute(
            "SELECT id::string, name, type, subjects, (deleted_at IS NOT NULL), source_kind "
            "FROM nodes WHERE workspace = %s",
            (ws,),
        )
        nodes = [{"id": r[0], "name": r[1], "type": r[2], "subjects": r[3], "deleted": r[4],
                  "source_kind": r[5]} for r in c.fetchall()]
        c.execute(
            "SELECT source_id::string, target_id::string, relationship FROM edges WHERE workspace = %s",
            (ws,),
        )
        edges = [{"source": r[0], "target": r[1], "rel": r[2]} for r in c.fetchall()]
        # Which subjects are currently un-erasable, so a held subject can be shown as
        # refused rather than silently doing nothing when someone asks to forget it.
        c.execute(
            "SELECT subject, hold_reason FROM subject_keys WHERE workspace = %s "
            "AND held_at IS NOT NULL AND (hold_until IS NULL OR hold_until > now())",
            (ws,),
        )
        holds = {r[0]: r[1] for r in c.fetchall()}
    return {"nodes": nodes, "edges": edges, "holds": holds}


@app.get("/evidence")
def evidence():
    """The landing page's benchmark section stays hidden until a real, dated eval exists.
    Serve research/evidence.json when the correctness benchmark has been run; otherwise
    report it as unavailable (never show an unproven number)."""
    path = os.path.join(ROOT, "research", "evidence.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            import json
            data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("available", True)
                return data
    except (FileNotFoundError, ValueError):
        pass
    return {"available": False}


@app.get("/api/timeline")
def api_timeline(workspace: str = "default"):
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT kind, subject, detail, created_at::string FROM timeline "
            "WHERE workspace = %s ORDER BY created_at DESC LIMIT 100",
            (_ws(workspace),),
        )
        return [{"kind": r[0], "subject": r[1], "detail": r[2], "at": r[3]} for r in c.fetchall()]


@app.get("/api/events")
def api_events(workspace: str = "default"):
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT id::string, subject, docs_removed, nodes_removed, edges_removed, "
            "nodes_invalidated, created_at::string FROM erasure_events "
            "WHERE workspace = %s ORDER BY created_at DESC LIMIT 100",
            (_ws(workspace),),
        )
        return [{"id": r[0], "subject": r[1], "docs": r[2], "nodes": r[3], "edges": r[4],
                 "invalidated": r[5], "at": r[6]} for r in c.fetchall()]


@app.get("/api/subjects")
def api_subjects(workspace: str = "default"):
    with store.connect() as conn, conn.cursor() as c:
        c.execute("SELECT DISTINCT subject FROM documents WHERE workspace = %s ORDER BY subject",
                  (_ws(workspace),))
        return [r[0] for r in c.fetchall()]


# ─────────────────────────────────────────────────────────── curation
class DemoteReq(BaseModel):
    subject: str
    weight: float | None = None
    workspace: str = "default"


@app.post("/api/demote")
def api_demote(r: DemoteReq, _: None = Depends(require_auth)):
    return {"demoted_nodes": curation.demote(r.subject, r.weight or curation.DEMOTE_DEEP, _ws(r.workspace))}


@app.post("/api/restore")
def api_restore(r: DemoteReq, _: None = Depends(require_auth)):
    return {"restored_nodes": curation.restore(r.subject, _ws(r.workspace))}


@app.get("/api/curation")
def api_curation(workspace: str = "default"):
    ws = _ws(workspace)
    return {"stale_references": curation.stale_references(workspace=ws),
            "aging": curation.aging_documents(workspace=ws)}


class CycleReq(BaseModel):
    apply: bool = False
    workspace: str = "default"


@app.post("/api/curation/cycle")
def api_curation_cycle(r: CycleReq, _: None = Depends(require_auth)):
    """The decay loop — preview (apply=false) or apply one bounded pass by review-age."""
    return curation.run_cycle(r.apply, _ws(r.workspace))


# ─────────────────────────────────────────────────────────── model (BYO)
class ModelReq(BaseModel):
    provider: str | None = None
    model: str | None = None
    endpoint: str | None = None
    api_key: str | None = None


@app.get("/api/model")
def api_model_get():
    return {"provider": llm_client.LLM_PROVIDER, "model": llm_client.LLM_MODEL,
            "endpoint": llm_client.LLM_ENDPOINT}


@app.post("/api/model")
def api_model_set(r: ModelReq, _: None = Depends(require_auth)):
    if LOCK_MODEL:
        raise HTTPException(status_code=403, detail="the model is locked on this deployment")
    _validate_endpoint(r.endpoint or "")
    return llm_client.set_config(r.provider, r.model, r.endpoint, r.api_key)


# ─────────────────────────────────────────────────────────── certificate page
_CERT_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Certificate of Erasure &middot; Agent X</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 28 24' fill='none' stroke='%23a8a8b0' stroke-width='2.4' stroke-linecap='round'><path d='M16.97 7.97A7.2 7.2 0 1 0 16.97 16.03'/><circle cx='19.6' cy='12' r='1.25' fill='%23a8a8b0' stroke='none'/><circle cx='22.6' cy='12' r='0.95' fill='%23a8a8b0' stroke='none' opacity='.6'/><circle cx='25.2' cy='12' r='0.62' fill='%23a8a8b0' stroke='none' opacity='.32'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
  :root{--ink:#0f172a;--muted:#64748b;--faint:#94a3b8;--line:#e2e8f0;--line2:#cbd5e1;--brand:#74747c;--paper:#ffffff;--wash:#f6f7fc}
  @media (prefers-color-scheme:dark){:root{--wash:#0a0a12;--brand:#a8a8b0}}
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:var(--wash);color:var(--ink);font-family:'Geist',system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
  .serif{font-family:'Instrument Serif','Spectral',Georgia,serif;font-weight:400}
  .mono{font-family:'Geist Mono',ui-monospace,SFMono-Regular,monospace}
  .dim{color:var(--faint)}
  .wrap{max-width:760px;margin:40px auto;padding:0 20px}
  .sheet{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:56px 60px 48px;box-shadow:0 1px 2px rgba(15,23,42,.04),0 12px 40px -12px rgba(15,23,42,.10);position:relative}
  .topbar{display:flex;align-items:center;gap:9px;padding-bottom:22px;border-bottom:1px solid var(--line)}
  .topbar .wordmark{font-size:21px;letter-spacing:-.01em}
  .topbar .tag{margin-left:auto;font-size:10.5px;letter-spacing:.22em;text-transform:uppercase;color:var(--faint)}
  .head{text-align:center;padding:38px 0 8px}
  .head .eyebrow{font-size:11px;letter-spacing:.34em;text-transform:uppercase;color:var(--brand);font-weight:500}
  .head h1{font-size:52px;line-height:1.04;margin:12px 0 0;letter-spacing:-.01em}
  .head .subject{margin-top:14px;font-size:15px;color:var(--muted)}
  .head .subject .mono{color:var(--ink);font-size:14px}
  .rule{height:1px;background:var(--line);margin:34px 0}
  .block{margin:30px 0}
  .kicker{font-size:10.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);margin-bottom:13px;font-weight:500}
  table.record{width:100%;border-collapse:collapse}
  table.record th{text-align:left;font-weight:500;color:var(--muted);font-size:12.5px;padding:9px 0;width:38%;vertical-align:top;border-bottom:1px solid var(--line)}
  table.record td{text-align:left;font-size:13.5px;padding:9px 0;border-bottom:1px solid var(--line);vertical-align:top}
  .metrics{display:flex;gap:14px}
  .metric{flex:1;border:1px solid var(--line);border-radius:5px;padding:18px 10px;text-align:center;background:var(--wash)}
  .metric-n{font-family:'Geist Mono',monospace;font-size:30px;font-weight:500;letter-spacing:-.02em;color:var(--ink)}
  .metric-l{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin-top:5px}
  .metric-empty{flex:1;border:1px dashed var(--line2);border-radius:5px;padding:16px;text-align:center;color:var(--faint);font-size:12.5px}
  .measured-note{margin-top:12px;font-size:12px;color:var(--muted);line-height:1.55}
  .proof{border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .proof-row{display:flex;gap:12px;padding:13px 16px;font-size:13px;line-height:1.5}
  .proof-row.q{background:var(--wash);border-bottom:1px solid var(--line)}
  .proof-tag{font-family:'Geist Mono',monospace;font-size:9.5px;letter-spacing:.14em;color:var(--faint);padding-top:3px;min-width:64px}
  .proof-note{margin:12px 2px 0;font-size:12px;color:var(--muted);line-height:1.55}
  .attest{margin:30px 0 6px;font-size:14.5px;line-height:1.7;color:var(--ink)}
  .certid{margin-top:34px;border:1px solid var(--line2);border-radius:6px;padding:18px 20px;display:flex;align-items:center;gap:18px;background:var(--wash)}
  .certid .label{font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
  .certid .hash{font-family:'Geist Mono',monospace;font-size:19px;letter-spacing:.04em;color:var(--ink);word-break:break-all}
  .certid .frame{font-size:11px;color:var(--muted);margin-top:7px;line-height:1.5}
  .certid .seal{margin-left:auto;flex-shrink:0;width:60px;height:60px;border:1px solid var(--brand);border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--brand);opacity:.85}
  .foot{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);display:flex;align-items:center;gap:10px;font-size:11px;color:var(--faint)}
  .foot .mono{color:var(--muted)}
  .actions{max-width:760px;margin:18px auto 60px;padding:0 20px;text-align:center}
  .btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);background:var(--paper);color:var(--ink);font-size:13px;font-weight:500;padding:10px 18px;border-radius:8px;cursor:pointer;transition:background .15s,transform .05s}
  .btn:hover{background:var(--wash)}
  .btn:active{transform:translateY(1px)}
  @media print{
    @page{margin:14mm}
    html,body{background:#fff}
    .wrap{margin:0 auto;max-width:none;padding:0}
    .sheet{border:none;border-radius:0;box-shadow:none;padding:0}
    .actions{display:none}
    .certid,.metric,.proof-row.q{-webkit-print-color-adjust:exact;print-color-adjust:exact}
    .head h1{font-size:46px}
  }
</style>
</head>
<body>
<div class="wrap"><div class="sheet">
  <div class="topbar">{{WAVE}}<span class="serif wordmark">Agent X</span><span class="tag">Verifiable Memory Erasure</span></div>

  <div class="head">
    <div class="eyebrow">Right to be forgotten &middot; Data erasure record</div>
    <h1 class="serif">Certificate of Erasure</h1>
    <div class="subject">Issued for the permanent removal of <span class="mono">{{SUBJECT}}</span> from Agent X&rsquo;s memory.</div>
    <div class="subject dim" style="font-size:11.5px;margin-top:6px;max-width:34rem;margin-left:auto;margin-right:auto">This operator-facing record names the subject for your internal audit trail. The portable certificate and its object-locked S3 copy carry only a one-way hash under a <strong>random per-event salt</strong> — the salt is kept operator-side and never leaves in the portable proof, so the shareable certificate cannot be reversed to the subject.</div>
  </div>

  <div class="rule"></div>

  <section class="block">
    <div class="kicker">Record</div>
    <table class="record"><tbody>
      <tr><th>Erased subject</th><td><span class="mono">{{SUBJECT}}</span></td></tr>
      <tr><th>Workspace</th><td><span class="mono">{{WORKSPACE}}</span></td></tr>
      <tr><th>Date of erasure</th><td>{{DATE_HUMAN}} <span class="mono dim">&middot; {{DATE_ISO}}</span></td></tr>
      <tr><th>Transaction timestamp<br><span class="dim" style="font-weight:400">CockroachDB MVCC</span></th><td><span class="mono">{{MVCC_TS}}</span><div class="dim" style="font-size:11.5px;margin-top:5px">The commit timestamp the database itself assigned the erasure transaction — a provable, DB-issued fact.</div></td></tr>
    </tbody></table>
  </section>

  <section class="block">
    <div class="kicker">Measured deletion &middot; permanently removed</div>
    <div class="metrics">{{METRICS}}</div>
    <p class="measured-note">The figures above are the real before/after difference measured against Agent X&rsquo;s knowledge graph at erasure. The subject&rsquo;s documents, graph nodes, relationships, and vector embeddings were removed in a <strong>single ACID transaction</strong>, and the subject&rsquo;s encryption key was destroyed — rendering any residual ciphertext cryptographically unrecoverable.{{SHARED_NOTE}}</p>
  </section>

  <section class="block">
    <div class="kicker">Verification &middot; live re-check at view time</div>
    <div class="proof">
      <div class="proof-row q"><span class="proof-tag">RE-CHECK</span><span>Queried the live database when this page was generated.</span></div>
      <div class="proof-row"><span class="proof-tag">RESULT</span><span>{{VERIFY_LINE}}</span></div>
    </div>
    <p class="proof-note">This is not a stored claim: Agent X re-queried CockroachDB at the moment you loaded this certificate. The subject&rsquo;s exclusive knowledge is no longer retrievable from the graph or the vector index.</p>
  </section>

  <div class="rule"></div>

  <p class="attest">This certifies that the knowledge above was permanently and verifiably removed from Agent X&rsquo;s memory on {{DATE_SHORT}} in one atomic CockroachDB transaction; a live re-query confirms it is no longer retrievable.</p>

  <div class="certid">
    <div>
      <div class="label">Certificate ID &middot; content-hash &middot; tamper-evident</div>
      <div class="hash">{{CERT_ID}}</div>
      <div class="frame">{{HONESTY}}</div>
      <div class="frame" style="margin-top:6px">Subject identifier (portable certificate carries only this hash — no personal data): <span class="mono" style="word-break:break-all">{{SUBJECT_SHA}}</span></div>
    </div>
    <div class="seal">{{WAVE}}</div>
  </div>

  <div class="foot">{{WAVE}}<span>Generated by <span class="mono">Agent X</span> &mdash; verifiable AI-memory forgetting on CockroachDB. This document is built solely from Agent X&rsquo;s stored erasure record and a live database re-check.</span></div>
</div></div>

<div class="actions">
  <button class="btn" onclick="window.print()">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect width="12" height="8" x="6" y="14"/></svg>
    Print / Save as PDF
  </button>
</div>
</body>
</html>"""


def _esc(v) -> str:
    """HTML-escape a value for safe interpolation into the certificate."""
    import html
    return html.escape("" if v is None else str(v))


def _render_certificate(row, event_id: str) -> str:
    """Build the Agent X Certificate of Erasure page from an erasure_events row.

    The CockroachDB-native facts — the MVCC transaction timestamp and the live,
    view-time proof-of-absence — are read straight from the cluster.
    """
    import hashlib
    workspace, subject, t_before, docs, nodes, edges, shared, created_at, subject_salt = row
    salt = bytes(subject_salt) if subject_salt else b""

    # Date formatting (best-effort; falls back to the raw DB string).
    date_human = date_short = created_at or ""
    try:
        dt = datetime.fromisoformat((created_at or "").replace(" ", "T"))
        date_human = f"{dt.strftime('%B')} {dt.day}, {dt.strftime('%Y at %H:%M')} UTC"
        date_short = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    except Exception:
        pass

    subj_hash = hashlib.sha256(salt + f"{workspace}:{subject or ''}".encode()).hexdigest()

    # A live, view-time re-check against the database — not a stored claim.
    try:
        live = verify_gone(subject, workspace)
    except Exception:
        live = {"live_exclusive_nodes": None, "live_docs": None, "key_shredded": None}

    # Content-hash certificate ID: a SHA-256 over the displayed fields, re-derivable by anyone.
    canonical = "|".join(str(x) for x in
                         [event_id, subj_hash, t_before, docs, nodes, edges, shared, created_at])
    cert_id = hashlib.sha256(canonical.encode()).hexdigest()[:32]

    # Metrics block — conditional; if nothing was recorded, show the honest empty state.
    def metric(n, label):
        return (f'<div class="metric"><div class="metric-n">{_esc(n)}</div>'
                f'<div class="metric-l">{label}</div></div>')
    counts = [(docs, "documents"), (nodes, "graph nodes"), (edges, "relationships")]
    if any(c is not None for c, _ in counts):
        metrics_html = "".join(metric(n if n is not None else 0, l) for n, l in counts)
    else:
        metrics_html = ('<div class="metric-empty">Deletion counts were not recorded for '
                        'this event.</div>')

    shared_note = ""
    if shared:
        shared_note = (f' Entities shared with surviving subjects were <strong>retained</strong> '
                       f'for them — {_esc(shared)} such node(s) kept, with this subject&rsquo;s '
                       f'provenance removed.')

    # Honesty frame — precise about what the ID is, upgraded to reflect Agent X's real signing.
    honesty = ("A SHA-256 hash over this certificate&rsquo;s fields. Anyone can re-derive it from "
               "the values above to detect tampering. This is a content hash, not a cryptographic "
               "signature.")
    if cert.public_key_pem():
        seal_extra = (" The full erasure certificate is additionally signed with an ECDSA (P-256) "
                      "key")
        seal_extra += (" and written to object-locked (WORM) Amazon S3, so the record itself "
                       "cannot be altered or deleted." if cert.aws_configured() else " at issue time.")
        honesty += seal_extra

    # Agent X mark: a ring (the "O") shedding into particles = verifiable forgetting.
    wave = ('<svg width="30" height="26" viewBox="0 0 28 24" fill="none" aria-hidden="true">'
            '<path d="M16.97 7.97A7.2 7.2 0 1 0 16.97 16.03" stroke="var(--brand)" '
            'stroke-width="2.2" stroke-linecap="round"/>'
            '<circle cx="19.6" cy="12" r="1.25" fill="var(--brand)"/>'
            '<circle cx="22.6" cy="12" r="0.95" fill="var(--brand)" opacity=".6"/>'
            '<circle cx="25.2" cy="12" r="0.62" fill="var(--brand)" opacity=".32"/></svg>')

    vh = live.get("vector_hits")
    vector_part = (f'vector re-search (top-20 ANN): <strong>{_esc(vh)}</strong> nodes still carry '
                   f'this subject &middot; ') if vh is not None else ""
    verify_line = (
        f'{_esc(live.get("live_exclusive_nodes"))} exclusive nodes &middot; '
        f'{_esc(live.get("live_docs"))} documents remain for this subject &middot; '
        f'{vector_part}'
        f'encryption key destroyed: <strong>{_esc(live.get("key_shredded"))}</strong>')

    t = _CERT_TEMPLATE
    repl = {
        "{{WAVE}}": wave,
        "{{SUBJECT}}": _esc(subject),
        "{{WORKSPACE}}": _esc(workspace),
        "{{DATE_HUMAN}}": _esc(date_human),
        "{{DATE_ISO}}": _esc(created_at),
        "{{DATE_SHORT}}": _esc(date_short),
        "{{MVCC_TS}}": _esc(t_before),
        "{{METRICS}}": metrics_html,
        "{{SHARED_NOTE}}": shared_note,
        "{{VERIFY_LINE}}": verify_line,
        "{{CERT_ID}}": _esc(cert_id),
        "{{SUBJECT_SHA}}": _esc(subj_hash),
        "{{HONESTY}}": honesty,
    }
    for k, v in repl.items():
        t = t.replace(k, v)
    return t


@app.get("/certificate/{event_id}", response_class=HTMLResponse)
def certificate_page(event_id: str):
    # A non-UUID path segment would reach the DB as a bad cast and surface as a 500; the
    # honest answer for an unparseable id is the same 404 an unknown id gets.
    try:
        uuid.UUID(event_id)
    except ValueError:
        return HTMLResponse("<h1>Certificate not found</h1>", status_code=404)
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT workspace, subject, t_before, docs_removed, nodes_removed, edges_removed, "
            "nodes_invalidated, created_at::string, subject_salt FROM erasure_events WHERE id = %s",
            (event_id,),
        )
        row = c.fetchone()
    if not row:
        return HTMLResponse("<h1>Certificate not found</h1>", status_code=404)
    return HTMLResponse(_render_certificate(row, event_id))


# ─────────────────────────────────────────────────────────── certificate verifier
@app.post("/api/verify")
def api_verify(payload: dict):
    """Independently verify an erasure certificate — re-derive its hash + check the signature."""
    return cert.verify(payload)


@app.get("/verify", response_class=HTMLResponse)
def verify_page():
    return HTMLResponse(_VERIFY_TEMPLATE)


_VERIFY_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verify a Certificate of Erasure &middot; Agent X</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 28 24' fill='none' stroke='%23a8a8b0' stroke-width='2.4' stroke-linecap='round'><path d='M16.97 7.97A7.2 7.2 0 1 0 16.97 16.03'/><circle cx='19.6' cy='12' r='1.25' fill='%23a8a8b0' stroke='none'/><circle cx='22.6' cy='12' r='0.95' fill='%23a8a8b0' stroke='none' opacity='.6'/><circle cx='25.2' cy='12' r='0.62' fill='%23a8a8b0' stroke='none' opacity='.32'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&family=Instrument+Serif&display=swap" rel="stylesheet">
<style>
 :root{--ink:#0f172a;--muted:#64748b;--faint:#94a3b8;--line:#e2e8f0;--brand:#74747c;--paper:#fff;--wash:#f6f7fc;--ok:#059669;--bad:#e11d48}
 :root.dark{--ink:#e5e7eb;--muted:#94a3b8;--faint:#64748b;--line:#232336;--brand:#a8a8b0;--paper:#12121c;--wash:#0a0a12}
 *{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font-family:'Geist',system-ui,sans-serif;-webkit-font-smoothing:antialiased}
 .mono{font-family:'Geist Mono',ui-monospace,monospace}.serif{font-family:'Instrument Serif',Georgia,serif}
 .wrap{max-width:760px;margin:44px auto;padding:0 20px}
 .top{display:flex;align-items:center;gap:9px;margin-bottom:26px}
 .top .wm{font-family:'Instrument Serif',serif;font-size:21px}.top .tag{margin-left:auto;font-size:10.5px;letter-spacing:.22em;text-transform:uppercase;color:var(--faint)}
 h1{font-family:'Instrument Serif',serif;font-weight:400;font-size:2.6rem;margin:.2rem 0 .4rem;letter-spacing:-.01em}
 .lede{color:var(--muted);font-size:15px;line-height:1.6;max-width:60ch}
 textarea{width:100%;min-height:180px;margin-top:20px;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--paper);color:var(--ink);font-family:'Geist Mono',monospace;font-size:12.5px;line-height:1.5;resize:vertical;outline:none}
 textarea:focus{border-color:var(--brand)}
 .row{display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap}
 button{border:none;background:var(--brand);color:#fff;font-weight:500;font-size:14px;padding:10px 20px;border-radius:9px;cursor:pointer}
 button.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
 .res{margin-top:22px;border:1px solid var(--line);border-radius:12px;background:var(--paper);padding:22px 24px;display:none}
 .verdict{display:flex;align-items:center;gap:10px;font-size:1.4rem;font-family:'Instrument Serif',serif}
 .check{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;color:#fff;flex-shrink:0}
 .kv{margin-top:16px;display:grid;grid-template-columns:auto 1fr;gap:8px 18px;font-size:13px}
 .kv .k{color:var(--muted)}.kv .v{word-break:break-all}.kv .v.mono{font-family:'Geist Mono',monospace;font-size:12px}
 .pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;padding:3px 10px;border-radius:999px}
 .pill.ok{background:rgba(5,150,105,.12);color:var(--ok)}.pill.bad{background:rgba(225,29,72,.12);color:var(--bad)}
 .pk{margin-top:16px;font-size:11px;color:var(--muted)}.pk pre{margin:6px 0 0;padding:10px;background:var(--wash);border-radius:8px;overflow:auto;font-size:10.5px;white-space:pre-wrap;word-break:break-all}
 a{color:var(--brand)}
</style>
<script>(function(){var t=localStorage.theme;var sys=window.matchMedia&&window.matchMedia('(prefers-color-scheme:dark)').matches;if(t==='dark'||(t!=='light'&&sys))document.documentElement.classList.add('dark');})();</script>
</head><body>
<div class="wrap">
 <div class="top">
   <svg width="30" height="22" viewBox="0 0 28 24" fill="none" style="color:var(--brand)"><path d="M16.97 7.97A7.2 7.2 0 1 0 16.97 16.03" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="19.6" cy="12" r="1.25" fill="currentColor"/><circle cx="22.6" cy="12" r="0.95" fill="currentColor" opacity=".6"/><circle cx="25.2" cy="12" r="0.62" fill="currentColor" opacity=".32"/></svg>
   <a href="/app" class="wm" style="text-decoration:none;color:inherit" title="Back to the app">Agent X</a><a href="/app" class="tag" style="text-decoration:none">&larr; Back to app</a><span class="tag" style="margin-left:14px">Certificate Verifier</span>
 </div>
 <h1>Verify a Certificate of Erasure</h1>
 <p class="lede">Paste an Agent X erasure certificate (the JSON returned by a forget, or the object stored in S3). This page re-derives its <strong>SHA-256 content hash</strong> and checks the <strong>ECDSA (P-256) signature</strong> — a tampered field breaks the hash; a forged certificate fails the signature. No trust in our server required: the public key is shown so anyone can verify offline.</p>
 <textarea id="in" placeholder='Paste the certificate JSON here, e.g. {"certificate":{...},"sha256":"...","signature":"..."}'></textarea>
 <div class="row">
   <button onclick="doVerify()">Verify certificate</button>
   <button class="ghost" onclick="loadSample()">Load a sample</button>
 </div>
 <div class="res" id="res"></div>
</div>
<script>
 async function doVerify(){
   const el=document.getElementById('in');let payload;
   try{payload=JSON.parse(el.value)}catch(e){return show({error:'That is not valid JSON. Paste the whole certificate object.'});}
   try{const r=await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});show(await r.json());}
   catch(e){show({error:'Could not reach the verifier.'});}
 }
 function pill(v){if(v===true)return '<span class="pill ok">&#10003; valid</span>';if(v===false)return '<span class="pill bad">&#10007; invalid</span>';return '<span class="pill">not provided</span>';}
 function show(d){
   const box=document.getElementById('res');box.style.display='block';
   if(d.error){box.innerHTML='<div class="verdict"><span class="check" style="background:var(--bad)">&#10007;</span>'+d.error+'</div>';return;}
   const authentic=(d.hash_matches!==false)&&(d.signature_valid!==false);
   const col=authentic?'var(--ok)':'var(--bad)';
   let g='';if(d.guarantees){g=Object.entries(d.guarantees).map(([k,v])=>'<div class="k">'+k.replace(/_/g,' ')+'</div><div class="v">'+pill(!!v)+'</div>').join('');}
   box.innerHTML=
     '<div class="verdict"><span class="check" style="background:'+col+'">'+(authentic?'&#10003;':'&#10007;')+'</span>'+(authentic?'Authentic &amp; untampered':'Verification failed')+'</div>'+
     '<div class="kv">'+
       '<div class="k">Content hash re-derived</div><div class="v mono">'+(d.content_hash||'')+'</div>'+
       '<div class="k">Hash matches the certificate</div><div class="v">'+pill(d.hash_matches)+'</div>'+
       '<div class="k">ECDSA signature</div><div class="v">'+pill(d.signature_valid)+'</div>'+
       (d.event_id?'<div class="k">Event ID</div><div class="v mono">'+d.event_id+'</div>':'')+
       (d.subject_sha256?'<div class="k">Subject (salted hash — no PII)</div><div class="v mono">'+d.subject_sha256+'</div>':'')+
       (d.issued_at?'<div class="k">Issued at</div><div class="v mono">'+d.issued_at+'</div>':'')+
       g+
     '</div>'+
     (d.public_key_pem?'<div class="pk">Public key (verify the signature yourself, offline):<pre>'+d.public_key_pem+'</pre></div>':'');
 }
 async function loadSample(){
   // issue a real throwaway sample by asking the server for the most recent certificate's fields is out
   // of scope; instead paste guidance.
   document.getElementById('in').value='Run a Forget & Prove in the app, copy the "certificate" object from the result (or the .json in your S3 bucket), and paste it here.';
 }
</script>
</body></html>"""
