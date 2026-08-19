# Evaluation

Reproduce: `python evals/rrs.py` (runs live against CockroachDB).

## 1. Reconstruction-Robustness Score (RRS)

An attacker holds a leaked/backup copy of a record's ciphertext (as lingers in MVCC history or
backups) and tries to recover the plaintext *after* the record is deleted.

| Deletion method | Records recovered | RRS |
|-----------------|-------------------|-----|
| Naive delete (rows removed, key kept) | **6 / 6** | **0 %** |
| Agent X (delete + crypto-shred key) | **0 / 6** | **100 %** |

*Last run 2026-08-17 against the live CockroachDB cluster — reproduce with `python evals/rrs.py`.
Attack model: the adversary holds the raw ciphertext (a leaked backup/snapshot). Naive deletion keeps
the data key, so every record is still decryptable; crypto-shred destroys the key, so `decrypt_for`
returns `None` for all six.*

This mirrors *Ghost Vectors* (arXiv:2606.18497): API-deletion leaves embeddings physically
recoverable from disk (the paper reconstructs 25.5 % of exact names and 46.4 % of locations
from text embeddings, up to ~99 % from image embeddings); destroying the encryption key drops
recovery to 0 %.

## 2. Forget-correctness (behavioral)

Ingest six people, hard-forget three, and probe:

| Check | Result |
|-------|--------|
| Forgotten people — identifying data no longer surfaces in answers | **3 / 3** |
| Surviving people — data still answered correctly | **3 / 3** |

## Research grounding

- Naive deletion is ~18 % robust to reconstruction attacks; dependency-graph-aware *layered*
  deletion reaches ~94 % (*ForgetAgent*, IJRASET) — Agent X implements the layered approach.
- Crypto-shredding drives residual recovery to 0 % (*Ghost Vectors*, arXiv:2606.18497).
