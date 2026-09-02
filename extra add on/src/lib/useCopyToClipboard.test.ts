import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useCopyToClipboard } from "./useCopyToClipboard";

/**
 * These cover the failure this hook exists for: navigator.clipboard is only
 * available in a secure context and can be blocked by permissions policy or
 * an unfocused document. The previous bare `await writeText(...)` meant that
 * on any plain-HTTP deployment the copy button silently did nothing.
 */

function setSecureContext(value: boolean) {
  Object.defineProperty(window, "isSecureContext", { value, configurable: true, writable: true });
}

beforeEach(() => {
  setSecureContext(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCopyToClipboard", () => {
  it("starts idle", () => {
    const { result } = renderHook(() => useCopyToClipboard());
    expect(result.current.state).toBe("idle");
  });

  it("uses the async Clipboard API in a secure context", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("sha256:abc"); });

    expect(writeText).toHaveBeenCalledWith("sha256:abc");
    expect(result.current.state).toBe("copied");
  });

  it("falls back to execCommand when the Clipboard API rejects", async () => {
    // This is the real-world case: HTTP deployment, or permissions policy.
    const writeText = vi.fn().mockRejectedValue(new DOMException("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const exec = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });

    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("sha256:abc"); });

    expect(exec).toHaveBeenCalledWith("copy");
    expect(result.current.state).toBe("copied");
  });

  it("falls back when the context is not secure, without even trying the API", async () => {
    setSecureContext(false);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const exec = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });

    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("sha256:abc"); });

    expect(writeText).not.toHaveBeenCalled();
    expect(exec).toHaveBeenCalled();
    expect(result.current.state).toBe("copied");
  });

  it("reports failure visibly when both paths fail, instead of looking dead", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });

    const { result } = renderHook(() => useCopyToClipboard());
    let ok: boolean | undefined;
    await act(async () => { ok = await result.current.copy("sha256:abc"); });

    expect(ok).toBe(false);
    expect(result.current.state).toBe("failed");
  });

  it("does not leave the temporary textarea in the DOM", async () => {
    setSecureContext(false);
    Object.defineProperty(document, "execCommand", { value: () => true, configurable: true });

    const { result } = renderHook(() => useCopyToClipboard());
    await act(async () => { await result.current.copy("sha256:abc"); });

    expect(document.querySelectorAll("textarea")).toHaveLength(0);
  });

  it("returns to idle so the button can be used again", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const { result } = renderHook(() => useCopyToClipboard(50));
    await act(async () => { await result.current.copy("x"); });
    expect(result.current.state).toBe("copied");

    await waitFor(() => expect(result.current.state).toBe("idle"), { timeout: 1000 });
  });
});
