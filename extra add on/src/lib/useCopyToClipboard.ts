"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type CopyState = "idle" | "copied" | "failed";

/**
 * Copy-to-clipboard with a real fallback and a visible failure state.
 *
 * `navigator.clipboard` is only available in a secure context (HTTPS, or
 * localhost) and can additionally be blocked by permissions policy or by
 * the document not being focused. The previous call sites did a bare
 * `await navigator.clipboard.writeText(...)` with no catch, so on any
 * plain-HTTP deployment the promise rejected, the "copied" state never
 * ran, and the button looked simply dead - with an unhandled rejection in
 * the console. For a product whose pitch is "here is the hash, verify it
 * yourself", a silently broken copy button is a real defect.
 *
 * This tries the async Clipboard API, falls back to the legacy
 * execCommand path for non-secure contexts, and reports `failed` so the UI
 * can tell the user to select the text manually rather than pretending
 * nothing happened.
 */
export function useCopyToClipboard(resetAfterMs = 1500) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = useCallback(
    async (text: string) => {
      const schedule = (next: CopyState) => {
        setState(next);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setState("idle"), resetAfterMs);
      };

      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
          schedule("copied");
          return true;
        }
      } catch {
        // fall through to the legacy path below
      }

      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        // Keep it out of view and out of the tab order, but still selectable.
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.top = "-9999px";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        schedule(ok ? "copied" : "failed");
        return ok;
      } catch {
        schedule("failed");
        return false;
      }
    },
    [resetAfterMs]
  );

  return { state, copy };
}
