# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""
CrewAI instrumentation supporting `crewai`_, instrumented by
``CrewAIInstrumentor``.

.. _crewai: https://pypi.org/project/crewai/

Usage
-----

.. code:: python

    from crewai import Agent, Crew, Task
    from opentelemetry.instrumentation.crewai import CrewAIInstrumentor

    CrewAIInstrumentor().instrument()

    crew = Crew(agents=[...], tasks=[...])
    crew.kickoff()

This produces one ``crew.kickoff`` root span per run, with a nested
``agent.execute_task`` span per agent/task pair, ``llm.call`` and
``tool.call`` spans nested under those, and a span **link** (not a parent
span) between agents when one agent's task output feeds another.

Configuration
-------------

- ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`` -- set to ``true``
  to additionally capture prompt/response text and tool call
  arguments/results as span events and attributes. Off by default.

API
---
"""

from __future__ import annotations

from collections.abc import Collection
from importlib import import_module
from typing import Any

from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.instrumentation.utils import unwrap
from opentelemetry.trace import get_tracer
from wrapt import wrap_function_wrapper

from crewai.events.event_bus import crewai_event_bus
from crewai.events.types.llm_events import LLMCallCompletedEvent
from opentelemetry.instrumentation.crewai._wrappers import (
    on_llm_call_completed,
    wrap_agent_execute_task,
    wrap_crew_kickoff,
    wrap_llm_call,
    wrap_tool_run,
)
from opentelemetry.instrumentation.crewai.package import _instruments

# ``LLM.call`` is abstract on CrewAI's own BaseLLM -- every concrete
# provider implementation (the generic litellm-backed crewai.LLM, plus each
# native provider CrewAI ships) defines its own ``call``, so there is no
# single class whose method covers every provider. These are every
# concrete implementation as of crewai 1.15.5 (confirmed by reading the
# installed source, not assumed); provider modules for SDKs that aren't
# installed (e.g. boto3 for Bedrock) raise ImportError at import time, so
# each entry is patched best-effort and skipped if unavailable.
_LLM_CALL_CLASSES: tuple[tuple[str, str], ...] = (
    ("crewai", "LLM"),
    ("crewai.llms.providers.openai.completion", "OpenAICompletion"),
    ("crewai.llms.providers.anthropic.completion", "AnthropicCompletion"),
    ("crewai.llms.providers.gemini.completion", "GeminiCompletion"),
    ("crewai.llms.providers.azure.completion", "AzureCompletion"),
    ("crewai.llms.providers.bedrock.completion", "BedrockCompletion"),
)


class CrewAIInstrumentor(BaseInstrumentor):
    """An :class:`~opentelemetry.instrumentation.instrumentor.BaseInstrumentor`
    for CrewAI.

    Patches ``Crew.kickoff``, ``Agent.execute_task``, every concrete LLM
    provider's ``call`` (see ``_LLM_CALL_CLASSES``), and ``BaseTool.run`` at
    runtime to emit OTel spans; never modifies CrewAI's installed source.
    ``instrument()``/``uninstrument()`` are idempotent (inherited from
    ``BaseInstrumentor``): calling either twice in a row is a no-op on the
    second call.
    """

    def __init__(self) -> None:
        super().__init__()
        self._patched_llm_modules: list[tuple[str, str]] = []

    def instrumentation_dependencies(self) -> Collection[str]:
        """Return the ``crewai`` version range this instrumentor supports."""
        return _instruments

    def _instrument(self, **kwargs: Any) -> None:
        """Patch CrewAI's entry points and start listening for LLM call events."""
        tracer_provider = kwargs.get("tracer_provider")
        tracer = get_tracer(__name__, "", tracer_provider)

        crewai_event_bus.register_handler(LLMCallCompletedEvent, on_llm_call_completed)

        wrap_function_wrapper("crewai", "Crew.kickoff", wrap_crew_kickoff(tracer))
        wrap_function_wrapper(
            "crewai", "Agent.execute_task", wrap_agent_execute_task(tracer)
        )
        wrap_function_wrapper("crewai.tools", "BaseTool.run", wrap_tool_run(tracer))

        llm_call_wrapper = wrap_llm_call(tracer)
        self._patched_llm_modules = []
        for module_path, class_name in _LLM_CALL_CLASSES:
            try:
                import_module(module_path)
            except ImportError:
                continue  # optional provider SDK not installed
            wrap_function_wrapper(module_path, f"{class_name}.call", llm_call_wrapper)
            self._patched_llm_modules.append((module_path, class_name))

    def _uninstrument(self, **kwargs: Any) -> None:
        """Undo every patch applied by ``_instrument``."""
        import crewai  # noqa: PLC0415
        import crewai.tools  # noqa: PLC0415

        unwrap(crewai.Crew, "kickoff")
        unwrap(crewai.Agent, "execute_task")
        unwrap(crewai.tools.BaseTool, "run")

        for module_path, class_name in self._patched_llm_modules:
            module = import_module(module_path)
            unwrap(getattr(module, class_name), "call")
        self._patched_llm_modules = []

        crewai_event_bus.off(LLMCallCompletedEvent, on_llm_call_completed)
