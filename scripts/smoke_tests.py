"""
Agent X — CockroachDB Basic smoke tests.
Verifies the free-tier capabilities the whole architecture depends on, BEFORE we build.
Each check is isolated (autocommit) so one failure doesn't stop the rest.
"""
import os, sys, traceback
import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
URL = os.environ["DATABASE_URL"]

results = []
def check(name, fn):
    try:
        val = fn()
        results.append((name, "PASS", str(val)[:200]))
        print(f"  [PASS] {name}: {str(val)[:120]}")
    except Exception as e:
        results.append((name, "FAIL", str(e)[:300]))
        print(f"  [FAIL] {name}: {str(e)[:200]}")

def vec(n=384, seed=0.01):
    return "[" + ",".join(f"{(seed*(i+1))%1:.4f}" for i in range(n)) + "]"

def scalar(c):
    row = c.fetchone()
    assert row is not None
    return row[0]

print("Connecting to CockroachDB Basic...")
conn = psycopg.connect(URL, autocommit=True)
cur = conn.cursor()
print("Connected.\n--- SMOKE TESTS ---")

check("0. version", lambda: (cur.execute("SELECT version()"), scalar(cur))[1])
check("0b. db/user", lambda: (cur.execute("SELECT current_database(), current_user"), cur.fetchone())[1])

# 1. vector index feature flag
def vec_setting():
    cur.execute("SHOW CLUSTER SETTING feature.vector_index.enabled")
    return scalar(cur)
check("1. feature.vector_index.enabled (show)", vec_setting)

# 2. create VECTOR table + cosine vector index
def make_vec_table():
    cur.execute("DROP TABLE IF EXISTS _smoke_vec")
    cur.execute("CREATE TABLE _smoke_vec (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), embedding VECTOR(384))")
    cur.execute("CREATE VECTOR INDEX _smoke_idx ON _smoke_vec (embedding vector_cosine_ops)")
    return "table + cosine vector index created"
check("2. CREATE VECTOR INDEX (cosine)", make_vec_table)

# 3. insert + cosine ANN search
def ann_search():
    for _ in range(5):
        cur.execute("INSERT INTO _smoke_vec (embedding) VALUES (%s)", (vec(),))
    cur.execute("SELECT id FROM _smoke_vec ORDER BY embedding <=> %s LIMIT 3", (vec(),))
    return f"{len(cur.fetchall())} rows via cosine ANN"
check("3. cosine ANN query (<=>)", ann_search)

# 4. EXPLAIN — is the index used?
def explain_ann():
    cur.execute("EXPLAIN SELECT id FROM _smoke_vec ORDER BY embedding <=> %s LIMIT 3", (vec(),))
    plan = " | ".join(r[0].strip() for r in cur.fetchall() if r[0].strip())
    return plan[:250]
check("4. EXPLAIN ANN (index vs scan)", explain_ann)

# 5. AS OF SYSTEM TIME
def aost():
    cur.execute("SELECT count(*) FROM _smoke_vec AS OF SYSTEM TIME '-5s'")
    return f"AOST read ok, count={scalar(cur)}"
check("5. AS OF SYSTEM TIME", aost)

# 6. GC window (how far back AOST can read)
def gc_window():
    try:
        cur.execute("SHOW ZONE CONFIGURATION FROM RANGE default")
        rows = cur.fetchall()
        for r in rows:
            if "gc.ttlseconds" in str(r):
                return str(r)
        return str(rows)[:200]
    except Exception:
        cur.execute("SHOW ZONE CONFIGURATIONS")
        return str(cur.fetchall())[:200]
check("6. GC window (gc.ttlseconds)", gc_window)

# 7. recursive CTE (blast-radius graph walk)
def rec_cte():
    cur.execute("WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t WHERE n < 5) SELECT count(*) FROM t")
    return f"recursive CTE depth ok, count={scalar(cur)}"
check("7. recursive CTE", rec_cte)

# 8. row-level TTL
def row_ttl():
    cur.execute("DROP TABLE IF EXISTS _smoke_ttl")
    cur.execute("CREATE TABLE _smoke_ttl (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), expire_at TIMESTAMPTZ) WITH (ttl_expiration_expression = 'expire_at')")
    return "row-level TTL table created"
check("8. row-level TTL", row_ttl)

# 9. INSERT .. ON CONFLICT (deterministic dedup)
def upsert():
    cur.execute("DROP TABLE IF EXISTS _smoke_up")
    cur.execute("CREATE TABLE _smoke_up (name STRING PRIMARY KEY, hits INT DEFAULT 1)")
    cur.execute("INSERT INTO _smoke_up (name) VALUES ('x')")
    cur.execute("INSERT INTO _smoke_up (name) VALUES ('x') ON CONFLICT (name) DO UPDATE SET hits = _smoke_up.hits + 1")
    cur.execute("SELECT hits FROM _smoke_up WHERE name='x'")
    return f"upsert ok, hits={scalar(cur)}"
check("9. INSERT ON CONFLICT (dedup)", upsert)

# cleanup
for t in ("_smoke_vec", "_smoke_ttl", "_smoke_up"):
    try: cur.execute(f"DROP TABLE IF EXISTS {t}")
    except Exception: pass

print("\n--- SUMMARY ---")
p = sum(1 for _,s,_ in results if s=="PASS")
print(f"{p}/{len(results)} passed")
conn.close()

# emit machine-readable for the doc
import json
with open(os.path.join(os.path.dirname(__file__), "..", "docs", "_smoke_raw.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
