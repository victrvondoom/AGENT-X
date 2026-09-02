"""Real OpenTelemetry instrumentation for the agent fleet, using the GenAI
semantic conventions (gen_ai.system, gen_ai.request.model, gen_ai.agent.name)
that are the direct mechanism behind Google's "OpenTelemetry-compliant audit
logs" / Agent Observability requirement.

Exports to the console by default (works today, zero cloud dependency)
and additionally over OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set, so the
same spans reach a real collector - Cloud Trace, X-Ray, or anything else
speaking OTLP - without a single call site changing.
"""

from __future__ import annotations

import functools
import os
from typing import Callable, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_resource = Resource.create(
    {
        "service.name": "sentinel-agent-fleet",
        "service.namespace": "sentinel",
    }
)
_provider = TracerProvider(resource=_resource)

# Console is always on: it is what makes the traces visible in a demo with
# no collector running, and it costs nothing.
_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))


def _attach_otlp_exporter(provider: TracerProvider) -> str | None:
    """Additionally export over OTLP when an endpoint is configured.

    Deliberately opt-in via OTEL_EXPORTER_OTLP_ENDPOINT rather than always
    on: with no collector listening, the exporter retries in the background
    and floods the logs with connection errors, which in a live demo looks
    exactly like the agent fleet failing.

    Failure to attach is never fatal - losing telemetry must not take the
    fleet down with it.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        return endpoint
    except Exception as exc:  # noqa: BLE001 - telemetry is never load-bearing
        print(f"[observability] OTLP exporter not attached ({exc}); console tracing continues.")
        return None


OTLP_ENDPOINT = _attach_otlp_exporter(_provider)

trace.set_tracer_provider(_provider)

tracer = trace.get_tracer("sentinel.agents")

F = TypeVar("F", bound=Callable)


def traced_agent(agent_name: str, model: str | None = None) -> Callable[[F], F]:
    """Wraps an agent/tool function in a real OTel span carrying GenAI
    semantic-convention attributes, so every real agent action is traceable
    end to end - not a log line claiming observability exists."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(f"sentinel.agent.{agent_name}") as span:
                span.set_attribute("gen_ai.agent.name", agent_name)
                span.set_attribute("gen_ai.system", "sentinel")
                if model:
                    span.set_attribute("gen_ai.request.model", model)
                try:
                    result = fn(*args, **kwargs)
                    span.set_attribute("sentinel.status", "ok")
                    return result
                except Exception as exc:
                    span.set_attribute("sentinel.status", "error")
                    span.record_exception(exc)
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator
