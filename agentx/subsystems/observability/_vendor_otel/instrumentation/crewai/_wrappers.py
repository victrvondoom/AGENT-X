# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Method wrappers that turn CrewAI execution into OTel spans.

Each public ``_wrap_*`` function here is bound with ``functools.partial`` (or
a closure) to a ``tracer`` in :mod:`opentelemetry.instrumentation.crewai` and
installed with ``wrapt.wrap_function_wrapper``. They all follow the same
shape: start a span, set what attributes are available before calling the
wrapped method, call it, set the remaining attributes from the result, and on
exception record it on the span and re-raise unmodified.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

from opentelemetry.semconv._incubating.attributes import gen_ai_attributes
from opentelemetry.trace import Link, SpanKind, Status, StatusCode, Tracer

if TYPE_CHECKING:
    from crewai import Agent, Crew, Task
    from crewai.tools import BaseTool

_CAPTURE_CONTENT_ENV_VAR = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"


def _content_capture_enabled() -> bool:
    """Whether prompt/response/tool-argument capture is currently enabled.

    Checked at call time (not import time) so tests that flip the env var
    between calls see the change immediately.
    """
    return os.environ.get(_CAPTURE_CONTENT_ENV_VAR, "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# crew.kickoff
# ---------------------------------------------------------------------------


def wrap_crew_kickoff(tracer: Tracer):
    """Build the wrapper for ``Crew.kickoff``.

    Only the synchronous ``kickoff`` is patched. ``Crew.kickoff_async``
    delegates to ``self.kickoff`` via ``asyncio.to_thread`` (which copies the
    current ``contextvars.Context`` into the worker thread), so patching
    both would double-count every async run as two nested ``crew.kickoff``
    spans. Patching only the sync method covers both entry points correctly.
    """

    def _wrap_crew_kickoff(wrapped, instance: Crew, args: Any, kwargs: Any):
        agents = getattr(instance, "agents", None) or []
        tasks = getattr(instance, "tasks", None) or []
        process = getattr(instance, "process", None)
        with tracer.start_as_current_span(
            "crew.kickoff", kind=SpanKind.INTERNAL
        ) as span:
            span.set_attribute("crewai.crew.id", str(getattr(instance, "id", "")))
            span.set_attribute(
                "crewai.crew.process",
                getattr(process, "value", str(process)) if process else "",
            )
            span.set_attribute("crewai.crew.agent_count", len(agents))
            span.set_attribute("crewai.crew.task_count", len(tasks))
            _reset_handoff_state(instance)
            try:
                result = wrapped(*args, **kwargs)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                _clear_handoff_state(instance)
            return result

    return _wrap_crew_kickoff


# ---------------------------------------------------------------------------
# agent.execute_task (+ opt-in prompt capture, + handoff links)
# ---------------------------------------------------------------------------

# Per-crew-run handoff tracking: id(crew) -> {id(task): SpanContext of the
# agent.execute_task span that produced that task's output}. Kept on the
# instrumentation module rather than on the crewai objects themselves,
# because Crew/Task/Agent are pydantic models that reject arbitrary
# attribute assignment.
_handoff_spans: dict[int, dict[int, Any]] = {}
_handoff_lock = threading.Lock()


def _reset_handoff_state(crew: Crew) -> None:
    with _handoff_lock:
        _handoff_spans[id(crew)] = {}


def _clear_handoff_state(crew: Crew) -> None:
    with _handoff_lock:
        _handoff_spans.pop(id(crew), None)


def _record_handoff_span(crew: Crew | None, task: Task, span_context: Any) -> None:
    if crew is None or isinstance(crew, str):
        return
    with _handoff_lock:
        crew_spans = _handoff_spans.get(id(crew))
        if crew_spans is not None:
            crew_spans[id(task)] = span_context


def _resolve_handoff_links(crew: Crew | None, task: Task) -> list[Link]:
    """Resolve the span Links representing a handoff into ``task``.

    Mirrors ``Crew._get_context``: an explicit ``task.context`` list links
    only to those specific prior tasks; the default "not specified" sentinel
    links to every task executed so far in this crew run (matching CrewAI's
    own default of aggregating every prior task output); an explicit
    ``None``/empty list means no handoff at all.
    """
    if crew is None or isinstance(crew, str):
        return []
    with _handoff_lock:
        crew_spans = _handoff_spans.get(id(crew))
        if not crew_spans:
            return []

        task_context = getattr(task, "context", None)
        if not task_context:
            return []

        try:
            from crewai.utilities.constants import NOT_SPECIFIED
        except ImportError:
            NOT_SPECIFIED = object()  # pragma: no cover - defensive fallback

        if task_context is NOT_SPECIFIED:
            source_task_ids = list(crew_spans.keys())
        else:
            source_task_ids = [id(t) for t in task_context]

        return [
            Link(crew_spans[task_id])
            for task_id in source_task_ids
            if task_id in crew_spans
        ]


def wrap_agent_execute_task(tracer: Tracer):
    """Build the wrapper for ``Agent.execute_task``."""

    def _wrap_agent_execute_task(wrapped, instance: Agent, args: Any, kwargs: Any):
        task: Task | None = args[0] if args else kwargs.get("task")
        crew = getattr(instance, "crew", None)
        links = _resolve_handoff_links(crew, task) if task is not None else []

        with tracer.start_as_current_span(
            "agent.execute_task", kind=SpanKind.INTERNAL, links=links
        ) as span:
            span.set_attribute("crewai.agent.role", getattr(instance, "role", "") or "")
            if task is not None:
                span.set_attribute(
                    "crewai.task.description", str(getattr(task, "description", ""))
                )
                if _content_capture_enabled():
                    task_context = args[1] if len(args) > 1 else kwargs.get("context")
                    if task_context:
                        span.add_event(
                            "gen_ai.content.prompt", {"content": str(task_context)}
                        )
            try:
                result = wrapped(*args, **kwargs)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

            if _content_capture_enabled():
                span.add_event("gen_ai.content.completion", {"content": str(result)})

            if task is not None:
                _record_handoff_span(crew, task, span.get_span_context())
            return result

    return _wrap_agent_execute_task


# ---------------------------------------------------------------------------
# llm.call (GenAI semantic conventions)
# ---------------------------------------------------------------------------

# LLM.call() only returns text (or a tool result) -- usage, finish_reason,
# and the resolved model aren't in that return value, they're only on the
# LLMCallCompletedEvent CrewAI emits from inside the call. That emit() is
# fire-and-forget (crewai_event_bus dispatches sync handlers on a
# ThreadPoolExecutor and does not wait for them -- see
# CrewAIEventsBus.emit()), so there is no guarantee the event has already
# been handled by the time the wrapped call() returns. We correlate by
# id(instance) (the handler's ``source`` argument is the same LLM instance
# our wrapper is closed over) and wait briefly with a threading.Event,
# rather than relying on contextvars, which would not propagate a value set
# inside a different worker thread back to the caller's context.
#
# Each instance maps to a FIFO queue of waiters, not a single slot: CrewAI
# gives each agent its own LLM instance by default, so in the common case
# there's one waiter per instance and the queue never holds more than one
# entry. But if a user shares one LLM object across agents/tasks that run
# concurrently, two calls on the *same* instance can be in flight at once;
# a single overwritable slot would let the second call's registration clobber
# the first's, delivering the wrong usage data to the wrong span. A queue
# means overlapping calls each get their own waiter instead of colliding.
# This still assumes completion events arrive in the same order calls were
# dispatched, which holds unless two concurrent calls on one shared instance
# finish out of order -- a narrow edge case that's a documented limitation,
# not a crash: attributes just end up on the wrong (but still valid) span.
_pending_llm_calls: dict[int, deque[tuple[threading.Event, list[Any]]]] = {}
_pending_lock = threading.Lock()
_LLM_EVENT_WAIT_SECONDS = 2.0


def on_llm_call_completed(source: Any, event: Any) -> None:
    """crewai_event_bus listener: hand a completed call's usage/finish data
    to the oldest still-waiting ``_wrap_llm_call`` invocation for this
    instance."""
    with _pending_lock:
        queue = _pending_llm_calls.get(id(source))
        entry = queue.popleft() if queue else None
    if entry is not None:
        wait_event, holder = entry
        holder.append(event)
        wait_event.set()


def wrap_llm_call(tracer: Tracer):
    """Build the wrapper for ``LLM.call``."""

    def _wrap_llm_call(wrapped, instance: Any, args: Any, kwargs: Any):
        messages = args[0] if args else kwargs.get("messages")
        with tracer.start_as_current_span("llm.call", kind=SpanKind.CLIENT) as span:
            span.set_attribute(
                gen_ai_attributes.GEN_AI_SYSTEM,
                str(
                    getattr(instance, "provider", None)
                    or getattr(instance, "model", "")
                ),
            )
            span.set_attribute(
                gen_ai_attributes.GEN_AI_REQUEST_MODEL,
                str(getattr(instance, "model", "")),
            )
            if _content_capture_enabled() and messages is not None:
                span.add_event("gen_ai.content.prompt", {"content": str(messages)})

            wait_event = threading.Event()
            holder: list[Any] = []
            entry = (wait_event, holder)
            with _pending_lock:
                _pending_llm_calls.setdefault(id(instance), deque()).append(entry)
            try:
                result = wrapped(*args, **kwargs)
            except Exception as exc:
                with _pending_lock:
                    _discard_pending_entry(id(instance), entry)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

            # Success path only: emit() dispatches the completion event to a
            # thread pool asynchronously, so it may not have arrived yet even
            # though call() itself has already returned. Wait briefly, then
            # stop listening for it either way.
            wait_event.wait(timeout=_LLM_EVENT_WAIT_SECONDS)
            with _pending_lock:
                _discard_pending_entry(id(instance), entry)
            completed_event = holder[0] if holder else None
            _set_llm_response_attributes(span, completed_event)
            if _content_capture_enabled():
                span.add_event("gen_ai.content.completion", {"content": str(result)})

            return result

    return _wrap_llm_call


def _discard_pending_entry(
    instance_id: int, entry: tuple[threading.Event, list[Any]]
) -> None:
    """Remove ``entry`` from its instance's waiter queue if it's still there
    (already-serviced entries are gone) and drop the queue once it's empty,
    so ``_pending_llm_calls`` never accumulates stale keys."""
    queue = _pending_llm_calls.get(instance_id)
    if queue is None:
        return
    try:
        queue.remove(entry)
    except ValueError:
        pass  # already popped by on_llm_call_completed
    if not queue:
        del _pending_llm_calls[instance_id]


def _set_llm_response_attributes(span: Any, event: Any) -> None:
    if event is None:
        return
    usage = getattr(event, "usage", None) or {}
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    if input_tokens is not None:
        span.set_attribute(gen_ai_attributes.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    if output_tokens is not None:
        span.set_attribute(gen_ai_attributes.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
    finish_reason = getattr(event, "finish_reason", None)
    if finish_reason:
        span.set_attribute(
            gen_ai_attributes.GEN_AI_RESPONSE_FINISH_REASONS, [str(finish_reason)]
        )


# ---------------------------------------------------------------------------
# tool.call
# ---------------------------------------------------------------------------


def wrap_tool_run(tracer: Tracer):
    """Build the wrapper for ``BaseTool.run``.

    ``run`` (not ``_run``) is the stable public entry point: every tool
    subclass implements ``_run`` differently, but ``run`` is the single
    common method every tool call passes through.
    """

    def _wrap_tool_run(wrapped, instance: BaseTool, args: Any, kwargs: Any):
        with tracer.start_as_current_span("tool.call", kind=SpanKind.INTERNAL) as span:
            span.set_attribute(
                gen_ai_attributes.GEN_AI_TOOL_NAME, getattr(instance, "name", "") or ""
            )
            if _content_capture_enabled():
                span.set_attribute(
                    gen_ai_attributes.GEN_AI_TOOL_CALL_ARGUMENTS,
                    str({"args": args, "kwargs": kwargs}),
                )
            try:
                result = wrapped(*args, **kwargs)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

            if _content_capture_enabled():
                span.set_attribute(
                    gen_ai_attributes.GEN_AI_TOOL_CALL_RESULT, str(result)
                )
            return result

    return _wrap_tool_run
