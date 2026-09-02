import { describe, it, expect } from "vitest";
import { parseUnifiedDiff, truncateHash, formatTimestampUtc } from "./format";

/**
 * parseUnifiedDiff renders the real PatchProposal.diff produced by Python's
 * difflib. If it misclassifies a line, the Evidence Report shows a patch
 * that differs from the one actually committed to the fix branch - the
 * report would be describing code that was never applied.
 */
describe("parseUnifiedDiff", () => {
  const REAL_DIFF = [
    "--- a/lib/insecurity.ts",
    "+++ b/lib/insecurity.ts",
    "@@ -186,7 +186,7 @@",
    " export const updateAuthenticatedUsers = () => {",
    "   const token = req.cookies.token",
    "-    jwt.verify(token, publicKey, (err, decoded) => {",
    "+    jwt.verify(token, publicKey, { algorithms: ['RS256'] }, (err, decoded) => {",
    "       if (err === null) {",
  ].join("\n");

  it("classifies headers, context, additions and removals", () => {
    const lines = parseUnifiedDiff(REAL_DIFF);
    expect(lines.filter((l) => l.kind === "header")).toHaveLength(3);
    expect(lines.filter((l) => l.kind === "removed")).toHaveLength(1);
    expect(lines.filter((l) => l.kind === "added")).toHaveLength(1);
    expect(lines.filter((l) => l.kind === "context").length).toBeGreaterThan(0);
  });

  it("strips only the leading +/- marker, preserving code indentation", () => {
    const lines = parseUnifiedDiff(REAL_DIFF);
    const added = lines.find((l) => l.kind === "added")!;
    expect(added.text).toBe("    jwt.verify(token, publicKey, { algorithms: ['RS256'] }, (err, decoded) => {");
    expect(added.text.startsWith("+")).toBe(false);
  });

  it("does not mistake the --- / +++ file headers for removals or additions", () => {
    const lines = parseUnifiedDiff(REAL_DIFF);
    expect(lines[0]).toEqual({ kind: "header", text: "--- a/lib/insecurity.ts" });
    expect(lines[1]).toEqual({ kind: "header", text: "+++ b/lib/insecurity.ts" });
  });

  it("returns nothing for an empty diff rather than throwing", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });

  it("drops blank lines instead of emitting empty rows", () => {
    expect(parseUnifiedDiff("+a\n\n-b")).toHaveLength(2);
  });
});

describe("truncateHash", () => {
  it("leaves short values intact", () => {
    expect(truncateHash("abc")).toBe("abc");
  });

  it("keeps head and tail so a human can still compare two hashes", () => {
    const full = "sha256:" + "a".repeat(64);
    const short = truncateHash(full);
    expect(short).toContain("…");
    expect(full.startsWith(short.split("…")[0])).toBe(true);
    expect(full.endsWith(short.split("…")[1])).toBe(true);
  });
});

describe("formatTimestampUtc", () => {
  it("renders an ISO timestamp in explicit UTC", () => {
    expect(formatTimestampUtc("2026-08-21T03:55:48Z")).toBe("2026-08-21 03:55:48 UTC");
  });

  it("normalizes an offset timestamp to UTC rather than local time", () => {
    // Same instant, written with an offset - must not shift with the
    // machine's timezone, or two viewers would disagree about when an
    // agent acted.
    expect(formatTimestampUtc("2026-08-21T05:55:48+02:00")).toBe("2026-08-21 03:55:48 UTC");
  });
});
