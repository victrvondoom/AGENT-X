"""
The provider registry — which integrations exist, and honestly in what mode.

Registration is explicit and startup-time. There is no plugin discovery, because
a provider that appears because a file was on the path is a provider nobody
authorised, and this registry decides what Agent X is allowed to touch in the world.

THE RULE THIS REGISTRY ENFORCES

A capability whose family has no provider is UNAVAILABLE, and the planner routes
around it. It never emits a step that would quietly do nothing, and Agent X never
tells a user an action happened when no integration existed to make it happen.
That is the whole of "do not fake integrations", implemented in one lookup.

LIVE PROVIDERS

None ship enabled. Real merchant APIs, a real mailbox, and a real browser agent
are all straightforward to add — implement the same `Provider` interface, declare
`mode = "live"`, and call `register()`. The sandbox providers are not stand-ins
for a missing implementation; they are a deterministic external world that the
same engine drives through the same interface. Swapping one for the other is a
registration change and nothing else, and `docs/AGENT_X_ARCHITECTURE.md` says so
where a reader will look for the caveat.
"""
from __future__ import annotations

import os
from threading import RLock

from .base import FAMILIES, Provider, ProviderError, ProviderResult, UnavailableProvider

_lock = RLock()
_registry: dict[str, list[Provider]] = {}
_by_id: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    if provider.family not in FAMILIES:
        raise ProviderError(f"unknown provider family {provider.family!r}")
    with _lock:
        _registry.setdefault(provider.family, [])
        if provider.id not in _by_id:
            _registry[provider.family].append(provider)
            _by_id[provider.id] = provider
    return provider


def clear() -> None:
    with _lock:
        _registry.clear()
        _by_id.clear()


def bootstrap(*, sandbox: bool | None = None) -> dict:
    """Register the providers this deployment is configured for.

    Sandbox providers are on by default and can be turned off with
    AGENT_X_SANDBOX=0 — which is how a deployment with real integrations makes
    certain no case can silently fall back to a simulated one.

    Live providers are the opposite of opt-out: each is registered only if ITS
    OWN configuration is present (`cls.configured()`), independent of the
    sandbox flag. A deployment can therefore run both side by side — useful for
    a demo that wants to show a live send next to sandbox scenarios — but a
    production deployment that wants ONLY real integrations should still set
    AGENT_X_SANDBOX=0, because two generically-`serves=("*",)` providers in the
    same family resolve to whichever registered first, and that ordering should
    never be the thing standing between a demo action and a real one.
    """
    if sandbox is None:
        sandbox = os.environ.get("AGENT_X_SANDBOX", "1") not in ("0", "false", "no")
    with _lock:
        if _by_id:
            return summary()
    if sandbox:
        from .sandbox_providers import ALL
        for cls in ALL:
            register(cls())
    from .live_providers import ALL as LIVE_ALL
    for cls in LIVE_ALL:
        if cls.configured():
            register(cls())
    return summary()


def _ensure() -> None:
    if not _by_id:
        bootstrap()


def _all() -> list[Provider]:
    return [p for fam in _registry.values() for p in fam]


def for_family(family: str, *, counterparty: str | None = None,
               action: str | None = None) -> list[Provider]:
    """Providers that can serve this request, most specific first.

    WHO the counterparty is outranks WHICH family the capability asked for, and
    that ordering is a safety property rather than a convenience. A capability
    declares `provider_family = "merchant"`; Streamly's provider is registered
    under `subscription`. Ranking by family first meant a refund request naming
    Streamly resolved to the only registered merchant provider — Kartly — and a
    user's dispute would have been sent to a company with no connection to it.

    So: a provider that explicitly names this counterparty wins outright, wherever
    it is registered. Only if none does do we fall back inside the family, and a
    provider that explicitly cannot serve a NAMED counterparty is excluded
    entirely rather than used as a last resort.
    """
    _ensure()

    def usable(ps):
        return [p for p in ps if not action or p.supports(action)]

    in_family = usable(_all() if family == "any" else list(_registry.get(family, [])))
    named_in_family = [p for p in in_family
                       if counterparty and "*" not in p.serves and p.can_serve(counterparty)]
    generic_in_family = [p for p in in_family if "*" in p.serves]

    if not counterparty:
        others = [p for p in in_family if p not in generic_in_family]
        return others + generic_in_family

    # Ordering, and the reason for each rung:
    #   1. the counterparty's own provider inside the requested family — exactly right
    #   2. a generic provider in the family (a browser, a mailbox) — right shape,
    #      any counterparty
    #   3. the counterparty's own provider in ANOTHER family — Streamly is registered
    #      under `subscription` and can still process a refund, and reaching it here
    #      is what stops a Streamly refund resolving to the only merchant provider
    #      registered, which happens to be Kartly
    # A provider that names a DIFFERENT counterparty never appears at any rung: a
    # wrong-company action is worse than no action.
    named_anywhere = [p for p in usable(_all())
                      if "*" not in p.serves and p.can_serve(counterparty)
                      and p not in named_in_family]
    return named_in_family + generic_in_family + named_anywhere


def resolve(family: str, *, counterparty: str | None = None,
            action: str | None = None) -> Provider:
    """The provider that will actually run, or an UnavailableProvider explaining why."""
    pool = for_family(family, counterparty=counterparty, action=action)
    if pool:
        return pool[0]
    reason = (f"No provider is registered that can {action!r} for a {family} "
              f"counterparty." if action else
              f"No provider is registered for the {family} family.")
    return UnavailableProvider(family, reason)


def availability(family: str, *, hint: str | None = None) -> dict:
    pool = for_family(family, counterparty=hint)
    if not pool:
        return {"available": False, "mode": "none",
                "reason": f"no provider registered for the {family} family"}
    modes = sorted({p.mode for p in pool})
    return {"available": True, "mode": modes[0] if len(modes) == 1 else "mixed",
            "providers": [p.id for p in pool],
            "reason": f"{len(pool)} provider(s) registered for {family}"}


def get(provider_id: str) -> Provider | None:
    _ensure()
    return _by_id.get(provider_id)


def summary() -> dict:
    _ensure()
    return {
        "families": {f: [p.describe() for p in ps] for f, ps in sorted(_registry.items())},
        "count": len(_by_id),
        "live_providers": [p.id for p in _by_id.values() if p.mode == "live"],
        "sandbox_providers": [p.id for p in _by_id.values() if p.mode == "sandbox"],
        "note": ("Sandbox providers simulate real external systems and are labelled "
                 "as such on every action, execution record and receipt. No result "
                 "from a sandbox provider is ever presented as a real-world action."),
    }


__all__ = ["Provider", "ProviderResult", "ProviderError", "UnavailableProvider",
           "FAMILIES", "register", "clear", "bootstrap", "for_family", "resolve",
           "availability", "get", "summary"]
