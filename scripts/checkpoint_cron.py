"""
Scheduled checkpoint publisher.

A checkpoint's value comes entirely from being published on a REGULAR cadence
before disputes happen — a checkpoint minted on demand, after the fact, proves
nothing (a forger could equally mint one on demand). This script is meant to run
under cron / a scheduled task, not by hand.

Usage:
    DATABASE_URL=postgresql://... python scripts/checkpoint_cron.py

Cron (hourly):
    0 * * * *  cd /path/to/agent-x && DATABASE_URL=... python scripts/checkpoint_cron.py >> checkpoint.log 2>&1

Prints the published root to stdout so a wrapper script can append it to a log
that lives OUTSIDE this database — git, a status page, a timestamping service.
Storing the root only here proves nothing, since this table could be rewritten too.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                            # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set.")
    raise SystemExit(1)

import psycopg                                            # noqa: E402
from core.trust import merkle                              # noqa: E402

conn = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
cp = merkle.checkpoint(conn, note=f"scheduled ({os.environ.get('CRON_LABEL', 'cron')})")
conn.close()

# One line, safe to append to an external log or pipe to a publisher.
print(f"{cp['created_at']}  root={cp['merkle_root']}  "
      f"leaves={cp['leaf_count']}  id={cp['checkpoint_id']}")
