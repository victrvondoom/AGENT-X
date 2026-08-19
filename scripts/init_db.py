"""
Initialize Agent X: generate app secrets (if missing), apply the schema, and self-test the
storage layer (encrypt/decrypt roundtrip, crypto-shred, vector insert + ANN).

Usage:  python scripts/init_db.py
"""
import os
import sys
import base64
import secrets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ENV = os.path.join(os.path.dirname(__file__), "..", ".env")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENV)


def _fill(name: str, value: str) -> bool:
    lines = open(ENV).read().splitlines()
    changed = False
    for i, ln in enumerate(lines):
        if ln.strip() == f"{name}=":
            lines[i] = f"{name}={value}"
            changed = True
    if changed:
        open(ENV, "w").write("\n".join(lines) + "\n")
    return changed


# Secrets must exist before store's crypto is used.
if not os.environ.get("AGENT_X_ROOT_KEY"):
    k = base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()
    _fill("AGENT_X_ROOT_KEY", k)
    os.environ["AGENT_X_ROOT_KEY"] = k
    print("• generated AGENT_X_ROOT_KEY")
if not os.environ.get("APP_SECRET"):
    s = secrets.token_urlsafe(32)
    _fill("APP_SECRET", s)
    os.environ["APP_SECRET"] = s
    print("• generated APP_SECRET")

from db import store  # noqa: E402

with store.connect() as conn:
    n = store.apply_schema(conn)
    print(f"• schema applied ({n} statements)")

    # 1) envelope encryption roundtrip
    blob = store.encrypt_for(conn, "_selftest_alice", "Alice bank account 1234, sort 55-66")
    assert store.decrypt_for(conn, "_selftest_alice", blob) == "Alice bank account 1234, sort 55-66"
    print("• encrypt/decrypt roundtrip OK")

    # 2) crypto-shred — content is unrecoverable after the key is destroyed
    store.crypto_shred(conn, "_selftest_alice")
    assert store.decrypt_for(conn, "_selftest_alice", blob) is None
    print("• crypto-shred OK (ciphertext unrecoverable after key destroyed)")

    # 3) vector insert + cosine ANN
    with conn.cursor() as cur:
        cur.execute("DELETE FROM nodes WHERE name = '_selftest_node'")
        vec = store.to_vector([0.01 * (i % 10) for i in range(384)])
        cur.execute(
            "INSERT INTO nodes (name, type, description, embedding) VALUES (%s,%s,%s,%s)",
            ("_selftest_node", "test", "a self-test node", vec),
        )
        cur.execute("SELECT name FROM nodes ORDER BY embedding <=> %s LIMIT 1", (vec,))
        assert cur.fetchone()[0] == "_selftest_node"
    print("• vector insert + cosine ANN OK")

    # 4) logical timestamp (AOST anchor)
    print(f"• cluster_logical_timestamp() = {store.logical_now(conn)}")

    # cleanup self-test rows
    with conn.cursor() as cur:
        cur.execute("DELETE FROM nodes WHERE name = '_selftest_node'")
        cur.execute("DELETE FROM subject_keys WHERE subject = '_selftest_alice'")

print("\nM1 OK — storage layer live on CockroachDB.")
