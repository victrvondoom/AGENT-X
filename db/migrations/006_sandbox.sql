-- ═══════════════════════════════════════════════════════════════════════════
-- 006_sandbox.sql — state for the sandbox external systems
--
-- The sandbox is not a set of mock buttons. It is five companies with their own
-- records, their own refusal policies and their own response times, and a company
-- that forgets it cancelled your subscription is not a company — so its state has
-- to persist between calls exactly like a real one's does.
--
-- Kept in the same database as the cases on purpose. A judge inspecting the
-- system should be able to see the airline's booking row and Agent X's case row
-- side by side and confirm that the case's claims match what the external system
-- actually holds — which is a much stronger demonstration than either alone.
--
-- `state` is canonical JSON in a TEXT column, for the same reason every other
-- payload in this schema is: the bytes that go in are the bytes that come out.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sandbox_objects (
    id          TEXT PRIMARY KEY,          -- company:kind:reference
    company     TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- booking | order | subscription | invoice | payment | claim | ticket
    reference   TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sandbox_kind_idx ON sandbox_objects (company, kind);

-- The sandbox clock. A consumer resolution takes days, and a demo cannot. One
-- row, holding how far the sandbox world has been advanced past real time, so
-- "day 3, no response" is something a judge can watch happen in a second.
--
-- Deliberately separate from Agent X's own clock: production code always reads the
-- real one, and the demo passes an explicit `as_of` instead. A system whose
-- scheduler can be time-travelled from a global is a system whose scheduler
-- cannot be trusted.
CREATE TABLE IF NOT EXISTS sandbox_clock (
    id           TEXT PRIMARY KEY,
    offset_days  DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL
);

INSERT INTO schema_migrations (version) VALUES ('006_sandbox')
ON CONFLICT (version) DO NOTHING;
