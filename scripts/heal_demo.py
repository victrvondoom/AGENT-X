"""Self-heal the public demo.

The live demo lets anyone run Forget & Prove (that's the point). This means the seeded
`default` workspace can be emptied — by an honest judge trying the hero flow, or by anyone
malicious. This script rebuilds the demo when it has been wiped, and is a no-op (one cheap
COUNT) when the demo is intact, so it's safe to run on a short cron.

`seed_demo.main()` first clears the workspace's rows *including crypto-shredded subject_keys*,
so subjects that were forgotten (and would otherwise be blocked from re-onboarding with a 409)
come back cleanly.

Cron (every 10 min):
  */10 * * * * cd /home/ubuntu/agent-x && /home/ubuntu/.local/bin/uv run scripts/heal_demo.py >> /home/ubuntu/heal.log 2>&1
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import store  # noqa: E402

# The default workspace normally holds ~47 nodes. A single forget leaves ~40+; only a real
# wipe (forget-all / mass delete) drops below this, so the threshold catches wipes but not
# a judge trying one Forget & Prove.
THRESHOLD = 30


def _node_count() -> int:
    with store.connect() as conn, conn.cursor() as c:
        c.execute("SELECT count(*) FROM nodes WHERE workspace = 'default'")
        return c.fetchone()[0]


def main() -> None:
    n = _node_count()
    if n >= THRESHOLD:
        print(f"[heal] demo intact ({n} nodes) — nothing to do")
        return
    print(f"[heal] demo looks wiped ({n} nodes < {THRESHOLD}) — rebuilding…")
    import seed_demo
    seed_demo.main()
    print(f"[heal] rebuilt: {_node_count()} nodes")


if __name__ == "__main__":
    main()
