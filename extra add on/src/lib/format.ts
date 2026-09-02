export function truncateHash(hash: string, headLen = 8, tailLen = 6): string {
  if (hash.length <= headLen + tailLen + 1) return hash;
  return `${hash.slice(0, headLen)}…${hash.slice(-tailLen)}`;
}

export interface DiffLine {
  kind: "added" | "removed" | "context" | "header";
  text: string;
}

/** Parses a real unified-diff string (as produced by Python's difflib.unified_diff,
 * which is exactly what PatchProposal.diff contains) into colorable lines. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  if (!diff) return [];
  return diff
    .split("\n")
    .filter((line) => line.length > 0)
    .map((line): DiffLine => {
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
        return { kind: "header", text: line };
      }
      if (line.startsWith("+")) return { kind: "added", text: line.slice(1) };
      if (line.startsWith("-")) return { kind: "removed", text: line.slice(1) };
      return { kind: "context", text: line.startsWith(" ") ? line.slice(1) : line };
    });
}

export function formatTimestampUtc(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(
    d.getUTCMinutes()
  )}:${pad(d.getUTCSeconds())} UTC`;
}
