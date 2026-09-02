"""A real (if simple) reachability check: does the application's own source
actually import the vulnerable package, and if so, from where? This is the
MVP version called out in the backend build prompt - regex-based, not a full
call-graph engine, but it greps real files and returns real matches, not a
canned "reachability confirmed" string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
EXCLUDED_DIRS = {"node_modules", ".git", "dist", "build", "coverage", ".next"}
MAX_MATCHES = 12
MAX_FILES_SCANNED = 4000


@dataclass
class ReachabilityMatch:
    file: str
    line_number: int
    line_text: str


def _import_pattern(component: str) -> re.Pattern:
    escaped = re.escape(component)
    # matches: require('component'), require("component/sub"), import ... from 'component'
    return re.compile(rf"""(?:require\(\s*['"]{escaped}(?:/[^'"]*)?['"]\s*\)|from\s+['"]{escaped}(?:/[^'"]*)?['"])""")


def find_reachability_evidence(repo_dir: Path, component: str) -> list[ReachabilityMatch]:
    pattern = _import_pattern(component)
    matches: list[ReachabilityMatch] = []
    files_scanned = 0

    for path in repo_dir.rglob("*"):
        if len(matches) >= MAX_MATCHES or files_scanned >= MAX_FILES_SCANNED:
            break
        if path.is_dir() or path.suffix not in SOURCE_EXTENSIONS:
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue

        files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append(
                    ReachabilityMatch(
                        file=str(path.relative_to(repo_dir)),
                        line_number=line_number,
                        line_text=line.strip()[:200],
                    )
                )
                if len(matches) >= MAX_MATCHES:
                    break

    return matches
