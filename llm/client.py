"""
LLM + embeddings client.

Embeddings: fastembed (bge-small-en-v1.5, 384-d) — local, free, offline.
Generation: any OpenAI-compatible provider via LiteLLM — Ollama for local dev, or a hosted
provider (Groq / Cerebras / OpenRouter / Gemini) for deployment. Swap via .env, no code change.
"""
from __future__ import annotations

import os
import json
import re
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemma4:12b")
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "http://localhost:11434")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

# Per-task model routing. Each is a full LiteLLM model string ("groq/llama-3.1-8b-instant",
# "openai/gpt-4o", "gemini/gemini-1.5-flash"), read once at import time — deployment config,
# not part of the runtime BYO-model switch below. Unset (the default) means that task falls
# through to LLM_PROVIDER/LLM_MODEL exactly as before this existed: a single-model deployment
# is byte-for-byte unaffected. Set one to route just that task through a different model —
# a cheap/fast model for classification, a strong reasoner for planning, a vision-capable
# model for document/receipt extraction — without disturbing the rest.
TASK_MODEL_ENV = {
    "classify": "AGENT_X_MODEL_CLASSIFY",
    "plan": "AGENT_X_MODEL_PLAN",
    "extract": "AGENT_X_MODEL_EXTRACT",
}
TASK_MODELS = {task: os.environ.get(var, "") for task, var in TASK_MODEL_ENV.items()}


def set_config(provider: str | None = None, model: str | None = None,
               endpoint: str | None = None, api_key: str | None = None) -> dict:
    """Runtime BYO-model switch — update the active provider/model/endpoint/key."""
    global LLM_PROVIDER, LLM_MODEL, LLM_ENDPOINT, LLM_API_KEY
    if provider:
        LLM_PROVIDER = provider
    if model:
        LLM_MODEL = model
    if endpoint is not None:
        LLM_ENDPOINT = endpoint
    if api_key is not None:
        LLM_API_KEY = api_key
    return {"provider": LLM_PROVIDER, "model": LLM_MODEL, "endpoint": LLM_ENDPOINT}


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _embedder():
    try:
        from fastembed import TextEmbedding
        return TextEmbedding(model_name=EMBED_MODEL)
    except Exception:
        return None


def _hash_embed(text: str, dim: int = 384) -> list[float]:
    """Fast deterministic fallback vector when fastembed model is unavailable."""
    import hashlib
    words = (text or "").lower().split()
    vec = [0.0] * dim
    if not words:
        return vec
    for word in words:
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        val = ((h >> 8) % 1000) / 1000.0 - 0.5
        vec[idx] += val
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed(text: str) -> list[float]:
    emb = _embedder()
    if emb is not None:
        try:
            return list(map(float, next(emb.embed([text or ""]))))
        except Exception:
            pass
    return _hash_embed(text)


def embed_batch(texts: list[str]) -> list[list[float]]:
    emb = _embedder()
    if emb is not None:
        try:
            return [list(map(float, v)) for v in emb.embed(texts)]
        except Exception:
            pass
    return [_hash_embed(t) for t in texts]



# ─────────────────────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────────────────────
def _litellm_complete(model: str, messages: list[dict], temperature: float,
                      max_tokens: int, *, api_key: str | None = None,
                      endpoint: str | None = None) -> str:
    import litellm

    litellm.suppress_debug_info = True
    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if endpoint:
        kwargs["api_base"] = endpoint

    # Reasoning models (gpt-oss, o-series, etc.) spend tokens on hidden reasoning BEFORE the
    # answer. With a small budget they can exhaust it mid-reasoning and return EMPTY content.
    # Give them headroom and ask for short reasoning so the answer actually lands in `content`.
    ml = (model or "").lower()
    reasoning = any(t in ml for t in ("gpt-oss", "reason", "o1", "o3", "o4", "deepseek-r"))
    if reasoning:
        max_tokens = max(max_tokens, 4000)

    def _complete(extra):
        resp = litellm.completion(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, **kwargs, **extra,
        )
        msg = resp.choices[0].message
        out = (msg.content or "").strip()
        if not out:  # last-resort: some reasoning models leave the answer only in reasoning_content
            out = (getattr(msg, "reasoning_content", "") or "").strip()
        return out

    try:
        return _complete({"reasoning_effort": "low"} if reasoning else {})
    except Exception:
        return _complete({})  # provider rejected reasoning_effort — retry plain


def chat(system: str, user: str, temperature: float = 0.0, max_tokens: int = 1500,
         task: str | None = None) -> str:
    """One-shot chat completion. Ollama is called directly (LiteLLM mishandles reasoning
    models like gemma, dropping `content`); hosted providers go through LiteLLM.

    `task` ("classify" | "plan" | "extract") routes through that task's configured model
    when set (see TASK_MODELS above) — always via LiteLLM, since a task override is only
    ever meaningful for a hosted provider. Omitted or unconfigured falls through to the
    default LLM_PROVIDER/LLM_MODEL path, unchanged.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    task_model = TASK_MODELS.get(task or "", "")
    if task_model:
        return _litellm_complete(task_model, messages, temperature, max_tokens,
                                 api_key=LLM_API_KEY or None)

    if LLM_PROVIDER == "ollama":
        import httpx

        resp = httpx.post(
            f"{LLM_ENDPOINT}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": False,
                "think": False,  # skip reasoning tokens — we want the answer/JSON directly
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=300,
        )
        resp.raise_for_status()
        return (resp.json().get("message") or {}).get("content", "") or ""

    endpoint = LLM_ENDPOINT if LLM_ENDPOINT and LLM_PROVIDER in ("openai", "custom") else None
    return _litellm_complete(LLM_MODEL, messages, temperature, max_tokens,
                             api_key=LLM_API_KEY or None, endpoint=endpoint)


def chat_json(system: str, user: str, max_tokens: int = 2200,
             task: str | None = None) -> dict:
    """Chat and parse the first JSON object in the reply (robust to code fences / prose)."""
    raw = chat(system, user, temperature=0.0, max_tokens=max_tokens, task=task)
    return _extract_json(raw)


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    # strip code fences
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # fall back to the largest {...} span
    start, depth = None, 0
    for i, ch in enumerate(raw):
        if ch == "{":
            if start is None:
                start, depth = i, 0   # begin a fresh span, ignoring any earlier stray brace
            depth += 1
        elif ch == "}":
            if start is None:
                continue              # a closer with no opener is not part of any span
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except Exception:
                    start = None
    return {}
