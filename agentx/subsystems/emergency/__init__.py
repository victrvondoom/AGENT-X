"""
Emergency Response — triaging a report and getting it to the right responders.

Vendored from a working emergency-triage agent. `_engine.py` is that code
unmodified: the emergency taxonomy, the urgency assessment, the location
extraction, and the plan that routes a report to a response team.

DISPATCH IS NOT SOMETHING THIS TRACK DECIDES

Notifying responders is irreversible and consequential — a false dispatch costs
someone else the help they needed. So this track PREPARES a dispatch and Agent X's
governor decides whether it may be sent, exactly as it does for an outbound
letter or an escalation. There is no path here that notifies anyone without
passing `governor.assess()`, and the track declares `approve` autonomy for that
reason.

WITHOUT CREDENTIALS IT STILL TRIAGES

The taxonomy and urgency assessment are local. What needs configuration is the
planning engine and the notification channels — so an unconfigured deployment
can still classify and rank a report, and says plainly that it cannot send it.
That split is the original's and is worth keeping: knowing how urgent something
is has value even when you must make the call yourself.
"""
from __future__ import annotations

import os

REQUIRED = ("PORTIA_API_KEY",)
NOTIFIERS = ("SLACK_BOT_TOKEN",)


def _engine_module():
    """The vendored triage engine, imported lazily.

    Named to avoid colliding with the `_engine` submodule attribute that the
    import machinery binds onto this package.
    """
    import importlib
    return importlib.import_module("agentx.subsystems.emergency._engine")


def can_dispatch() -> bool:
    return all(os.environ.get(k) for k in REQUIRED) and any(
        os.environ.get(k) for k in NOTIFIERS)


def available() -> dict:
    """Triage is local; dispatch needs configuration. Both stated separately."""
    try:
        _engine_module()
        loaded = True
        detail = None
    except Exception as exc:
        loaded = False
        detail = str(exc)

    if not loaded:
        return {"available": False, "can_triage": False, "can_dispatch": False,
                "detail": f"The triage engine could not be loaded: {detail}. "
                          f"Its planning library is not installed."}

    dispatch = can_dispatch()
    return {
        "available": True,
        "can_triage": True,
        "can_dispatch": dispatch,
        "detail": ("Reports can be triaged and dispatched, subject to approval."
                   if dispatch else
                   "Reports can be triaged and ranked. Dispatch is unavailable "
                   "until a planning key and a notification channel are set — "
                   "Agent X will not claim to have notified anyone."),
        "approval": "Dispatch always requires human approval, however configured.",
    }


def emergency_types() -> list[str]:
    """The taxonomy the vendored engine classifies into."""
    try:
        module = _engine_module()
        return [e.value for e in module.EmergencyType]
    except Exception:
        return []
