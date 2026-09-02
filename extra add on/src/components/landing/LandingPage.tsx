"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { IconShieldLock, IconArrowRight, IconPlayerPlay, IconX } from "@tabler/icons-react";
import { getHealth, listEvidence } from "@/lib/sentinel/api";
import { FLEET, COLORS, type FleetNode } from "./fleet";

// The Three.js bundle is large and nothing above the fold depends on it, so
// first paint must never wait for it. ssr:false because there is no DOM or
// WebGL context on the server to render into.
const FleetScene = dynamic(() => import("./FleetScene"), {
  ssr: false,
  loading: () => null,
});

/**
 * Media query as an external store.
 *
 * useSyncExternalStore rather than useEffect + setState: a media query is
 * exactly the "external system React subscribes to" case, and it gives a
 * correct value on the very first render instead of a frame of the wrong
 * one - which for prefers-reduced-motion would mean briefly animating at
 * someone who explicitly asked not to be animated at.
 */
function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    [query]
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false // server: assume no preference, no WebGL rendered there anyway
  );
}

/**
 * Live telemetry along the bottom edge.
 *
 * Real numbers from the engine, or em-dashes when it is unreachable. It
 * deliberately does not fall back to invented "realistic" figures: this
 * whole product is built on the claim that nothing in it is fabricated,
 * and a judge who spots one made-up number on the landing page has every
 * reason to doubt the evidence pages too.
 */
function Telemetry() {
  const [stats, setStats] = useState<{ findings: string; verified: string; sealed: string }>({
    findings: "—",
    verified: "—",
    sealed: "—",
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [health, evidence] = await Promise.all([getHealth(), listEvidence()]);
        if (cancelled) return;
        setStats({
          findings: String(health.scan?.grounded ?? "—"),
          verified: String(health.evidence_verified_count ?? "—"),
          sealed: String(evidence.evidence.length),
        });
      } catch {
        // Engine unreachable - leave the dashes in place.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const items = [
    ["findings grounded", stats.findings],
    ["evidence verified", stats.verified],
    ["packages sealed", stats.sealed],
  ] as const;

  return (
    <div className="pointer-events-none flex flex-wrap items-center gap-x-6 gap-y-1 font-data text-[10px] uppercase tracking-[0.14em] text-text-dim">
      {items.map(([label, value]) => (
        <span key={label} className="flex items-center gap-2">
          <span className="text-text-muted">{value}</span>
          <span>{label}</span>
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="h-1 w-1 rounded-full" style={{ background: COLORS.verified }} />
        live from the agent engine
      </span>
    </div>
  );
}

export function LandingPage() {
  const router = useRouter();
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const small = useMediaQuery("(max-width: 820px)");
  const [hover, setHover] = useState<{ node: FleetNode; x: number; y: number } | null>(null);
  const [selected, setSelected] = useState<FleetNode | null>(null);
  const [flying, setFlying] = useState(false);
  const navigated = useRef(false);

  const go = useCallback(() => {
    if (navigated.current) return;
    navigated.current = true;
    router.push("/command-center");
  }, [router]);

  // "Get started" flies the camera into the constellation and hands over to
  // the Command Center's own agent graph, so it reads as one continuous
  // space. A second activation during the flight skips straight there -
  // nobody should be held hostage by an animation they have already seen.
  const start = useCallback(() => {
    if (flying) {
      go();
      return;
    }
    if (reduced || small) {
      go();
      return;
    }
    setFlying(true);
  }, [flying, go, reduced, small]);

  // Not called: on a static export there is no Next.js server to hand back
  // an RSC payload, so this always 404s - a real, once-per-visit console
  // error on the highest-traffic page in the app for zero benefit, since
  // "Get started" already navigates to a route the static host serves
  // directly.
  //
  // useEffect(() => {
  //   router.prefetch?.("/command-center");
  // }, [router]);

  // Escape closes the agent panel; the canvas never traps focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const animate = !reduced;

  return (
    <main className="relative h-dvh w-full overflow-hidden bg-bg text-text">
      {/* The scene is decorative: every word it conveys also exists as real
          DOM below, so screen readers and keyboard users lose nothing. */}
      <div aria-hidden className="absolute inset-0">
        <FleetScene
          animate={animate}
          simplified={small}
          flying={flying}
          onArrived={go}
          onHover={(node, screen) =>
            setHover(node && screen ? { node, x: screen.x, y: screen.y } : null)
          }
          onSelect={(n) => setSelected(n)}
          selectedId={selected?.id ?? null}
        />
      </div>

      {/* Vignette behind the copy. Text contrast must not depend on the
          scene happening to be dark wherever the headline sits. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(85% 80% at 16% 48%, rgba(10,12,16,0.95) 0%, rgba(10,12,16,0.80) 30%, rgba(10,12,16,0.34) 55%, rgba(10,12,16,0) 78%)",
        }}
      />

      {/* Top bar - matches the app's own. */}
      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between px-5 py-4 sm:px-8">
        <div className="flex items-center gap-2">
          <IconShieldLock size={18} strokeWidth={1.5} style={{ color: COLORS.amber }} />
          <span className="font-display text-[13px] font-medium uppercase tracking-[0.22em]">
            Sentinel
          </span>
        </div>
        <span className="hidden font-data text-[10px] uppercase tracking-[0.14em] text-text-dim sm:block">
          autonomous security verification
        </span>
      </header>

      {/* Headline + actions. Real, focusable DOM above the canvas. */}
      <section className="pointer-events-none relative z-10 flex h-full max-w-[46rem] flex-col justify-center px-5 sm:px-10 lg:px-16">
        <motion.h1
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          className="font-display text-[clamp(1.9rem,5.2vw,3.4rem)] font-semibold leading-[1.08] tracking-[-0.02em]"
        >
          Evidence-driven autonomous
          <br />
          security verification
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          className="mt-4 font-data text-[13px] tracking-[0.02em] text-text-muted sm:text-[14px]"
        >
          Prove it&rsquo;s broken. Fix it. Prove it&rsquo;s fixed.
        </motion.p>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.18 }}
          className="mt-5 max-w-[34rem] text-[13px] leading-relaxed text-text-dim sm:text-[14px]"
        >
          Six agents take a scanner finding, decide whether it is genuinely
          exploitable in your codebase, prove it in a sandbox, write the fix,
          re-test it, and seal the evidence.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.26, ease: [0.22, 1, 0.36, 1] }}
          className="pointer-events-auto mt-8 flex flex-wrap items-center gap-3"
        >
          <button
            type="button"
            onClick={start}
            className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
            style={{ background: COLORS.amber, borderColor: COLORS.amber, color: COLORS.bg }}
          >
            {flying ? "entering…" : "Get started"}
            <IconArrowRight
              size={13}
              strokeWidth={2}
              className="transition-transform group-hover:translate-x-0.5"
            />
          </button>

          <a
            href="https://github.com/rakeshselvaraj0108/SENTINEL"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 border border-border-soft px-5 py-2.5 font-data text-[11px] uppercase tracking-[0.14em] text-text-muted transition-colors hover:border-text-dim hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          >
            <IconPlayerPlay size={13} strokeWidth={1.6} />
            Watch the demo
          </a>
        </motion.div>

        {!small && (
          <p className="mt-6 font-data text-[10px] uppercase tracking-[0.14em] text-text-dim">
            hover a node to inspect an agent
          </p>
        )}
      </section>

      {/* Telemetry strip */}
      <div className="absolute inset-x-0 bottom-0 z-10 border-t border-border-soft/60 bg-bg/70 px-5 py-3 backdrop-blur-sm sm:px-8">
        <Telemetry />
      </div>

      {/* Hover tooltip - follows the cursor, never intercepts it. */}
      <AnimatePresence>
        {hover && !selected && (
          <motion.div
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.12 }}
            className="pointer-events-none fixed z-30 max-w-[15rem] border border-border-soft bg-bg/95 px-3 py-2 shadow-xl backdrop-blur"
            style={{ left: hover.x + 16, top: hover.y + 16 }}
          >
            <p className="font-data text-[10px] uppercase tracking-[0.12em]" style={{ color: COLORS.amber }}>
              {hover.node.label}
            </p>
            <p className="mt-1 text-[11px] leading-snug text-text-muted">
              {hover.node.responsibility}
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Agent detail panel - previews what the Governance page shows. */}
      <AnimatePresence>
        {selected && (
          <motion.aside
            role="dialog"
            aria-label={`${selected.label} details`}
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="absolute right-0 top-0 z-30 flex h-full w-full max-w-[22rem] flex-col gap-4 border-l border-border-soft bg-bg/95 p-5 backdrop-blur-md sm:w-[22rem]"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-data text-[10px] uppercase tracking-[0.14em] text-text-dim">
                  {selected.kind === "agent" ? "agent" : selected.kind}
                </p>
                <h2 className="mt-1 font-display text-[17px] font-medium">{selected.label}</h2>
              </div>
              <button className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
                type="button"
                onClick={() => setSelected(null)}
                aria-label="Close agent details"
                className="border border-border-soft p-1.5 text-text-dim transition-colors hover:border-text-dim hover:text-text"
              >
                <IconX size={13} strokeWidth={1.6} />
              </button>
            </div>

            <p className="text-[12.5px] leading-relaxed text-text-muted">{selected.detail}</p>

            <div>
              <p className="font-data text-[9.5px] uppercase tracking-[0.14em] text-text-dim">
                permitted scopes
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {selected.scopes.map((s) => (
                  <span
                    key={s}
                    className="border border-border-soft px-1.5 py-0.5 font-data text-[9.5px] text-text-muted"
                  >
                    {s}
                  </span>
                ))}
              </div>
            </div>

            <div>
              <p className="font-data text-[9.5px] uppercase tracking-[0.14em] text-text-dim">
                tools it may call
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {selected.tools.map((t) => (
                  <span
                    key={t}
                    className="border border-border-soft px-1.5 py-0.5 font-data text-[9.5px] text-text-muted"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>

            <p className="mt-auto font-data text-[9.5px] leading-relaxed text-text-dim">
              Every call above is checked against the Agent Registry and this
              agent&rsquo;s identity at the gateway before it runs.
            </p>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* Fly-in fade. The Command Center's own graph takes over underneath,
          so the two screens read as one continuous space. */}
      <AnimatePresence>
        {flying && (
          <motion.div
            aria-hidden
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 1.5, times: [0, 1], ease: "easeIn" }}
            className="pointer-events-none absolute inset-0 z-40"
            style={{ background: COLORS.bg }}
          />
        )}
      </AnimatePresence>

      {/* Skip affordance during the flight. */}
      {flying && (
        <button
          type="button"
          onClick={go}
          className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"
        >
          skip
        </button>
      )}

      {/* Hidden but real: the fleet as a list, so the page is complete with
          no WebGL at all. */}
      <div className="sr-only">
        <h2>Agent fleet</h2>
        <ol>
          {FLEET.map((n) => (
            <li key={n.id}>
              {n.label}: {n.responsibility}
            </li>
          ))}
        </ol>
      </div>
    </main>
  );
}
