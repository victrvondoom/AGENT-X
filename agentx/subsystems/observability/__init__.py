"""
Agent Observability — what Agent X's own agents actually did.

Vendored from a working OpenTelemetry instrumentation package. Its value is the
span model: an agent run, the tools it called, how long each took, what failed,
and how work handed off between agents — captured as standard OTel spans rather
than as a bespoke log format nobody else can read.

WHAT IS EXPOSED AND WHAT IS NOT

Execution telemetry, never reasoning. Spans carry which tool ran, how long it
took, whether it failed and why. They do not carry the model's intermediate
thinking, and they must not: a trace that leaks chain-of-thought turns an
operations tool into a disclosure channel.

EXPORT IS OFF UNLESS CONFIGURED

With no endpoint set, nothing is shipped anywhere and no exporter is installed.
That is a normal, complete state — Agent X works identically, there is simply
nowhere to send traces, and this reports that rather than pretending to collect.
"""
from __future__ import annotations

import os

# The vendored package instruments CrewAI specifically. Agent X does not run
# CrewAI, so that instrumentation activates only in a deployment that does —
# the span model and exporter wiring below are what Agent X itself uses.
_VENDOR = "agentx.subsystems.observability._vendor_otel.instrumentation.crewai"


def endpoint() -> str | None:
    return (os.environ.get("AGENT_X_OTEL_ENDPOINT")
            or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None)


def _api_present() -> bool:
    try:
        import opentelemetry.trace  # noqa: F401
        return True
    except Exception:
        return False


def available() -> dict:
    """Whether traces can be recorded, and whether they go anywhere."""
    if not _api_present():
        return {"available": False, "exporting": False,
                "detail": "The OpenTelemetry API is not installed, so no "
                          "telemetry is recorded."}
    target = endpoint()
    return {
        "available": True,
        "exporting": bool(target),
        "detail": ("Recording spans and exporting them to the configured "
                   "collector." if target else
                   "Recording spans in-process. No collector is configured, so "
                   "nothing is exported — set AGENT_X_OTEL_ENDPOINT to ship them."),
        "exposes": "tool calls, durations, failures and hand-offs",
        "never_exposes": "model reasoning",
    }


def tracer():
    """A tracer for Agent X's own spans, or None when unavailable."""
    if not _api_present():
        return None
    from opentelemetry import trace
    return trace.get_tracer("agentx")


def instrument_crewai() -> dict:
    """Activate the vendored CrewAI instrumentation, if CrewAI is in use.

    Only meaningful in a deployment that runs CrewAI agents. Reports honestly
    rather than raising when it is not applicable.
    """
    try:
        import crewai  # noqa: F401
    except Exception:
        return {"instrumented": False,
                "detail": "CrewAI is not installed; nothing to instrument."}
    try:
        import importlib
        module = importlib.import_module(_VENDOR)
        module.CrewAIInstrumentor().instrument()
        return {"instrumented": True, "detail": "CrewAI agent runs are traced."}
    except Exception as exc:
        return {"instrumented": False, "detail": f"Instrumentation failed: {exc}"}
