"""
Capability implementations — the engineering behind Agent X's tracks.

`agentx/tracks.py` declares what Agent X can do in a person's words. This
package holds the code that makes each of those true.

WHY THESE ARE VENDORED RATHER THAN REWRITTEN

Several capabilities arrived as working codebases of their own. The temptation
with an imported system is to read it, understand the idea, and reimplement a
tidier version — and that trade is almost always bad: the tidier version loses
the accumulated handling of cases nobody remembers hitting, and it costs weeks
to rediscover them.

So the rule here is that an imported implementation is preserved as closely as
possible and adapted only at its INTEGRATION BOUNDARY:

    changed     imports, so the module resolves inside this package
    changed     how it is mounted, so it serves under Agent X's app and auth
    changed     where it stores state, so it uses Agent X's data directory
    unchanged   the logic that makes the capability work

Where a file here differs from its original, the difference is a boundary fix
and is commented as one. A capability that needed real behavioural changes to
fit belongs in `agentx/` proper as native code, not here pretending to be
untouched.

EVERY CAPABILITY MUST DEGRADE ALONE

Nothing in this package may take Agent X down with it. Each subpackage is
imported lazily by its track and by its router, so a missing dependency, an
unreachable service or a broken vendored module surfaces as one track reporting
itself unavailable — never as an application that will not boot. `tracks.py`
resolves status by attempting exactly that import.
"""
