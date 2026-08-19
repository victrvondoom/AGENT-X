# Agent X — demo video storyboard (~2:50, 1600×900, 30fps)

Built on the proven Lethe pipeline. **Voice first** — you record the VO reading §B; then every
visual is timed to your words (faster-whisper anchors). The video must read **with sound off**, so
every beat carries a burned-in caption. Hero = forget → three proofs → certificate (longest slot).

Capture target: the **live** app `https://43-204-114-100.nip.io/` with the judge token pre-saved in
Settings → Security (so writes work). Two beats can't be automated (auth) — you grab them: the **S3
object-lock console** and the **MCP `select_query`** (both marked 🎙 below).

## A. Storyboard

| # | Time | On screen (capture) | Caption (burned in) | VO line |
|---|------|---------------------|---------------------|---------|
| 1 | 0:00–0:10 | Landing hero, slow scroll | **Agents are built to remember more.** | "Every agentic-memory project this cycle answers one question — how do agents *remember* more?" |
| 2 | 0:10–0:18 | Landing continues | **Almost none ask: how do they forget?** | "Almost none ask the harder one — how do they *forget*? Completely, and provably." |
| 3 | 0:18–0:26 | Name card (3D logo + wordmark) | **Agent X — verifiable forgetting · CockroachDB-native** | "This is Agent X. Verifiable forgetting for AI-agent memory, built on CockroachDB." |
| 4 | 0:26–0:36 | Knowledge graph (live physics) | **Graph + vectors + audit — one store.** | "A knowledge graph, semantic recall, and a tamper-evident audit trail — in one transactional store." |
| 5 | 0:36–0:48 | Chat: ask "what should I check for payments-service?" → grounded answer + source chips | **Grounded answers, with sources.** | "It answers strictly from what it knows, and cites its sources." |
| 6 | 0:48–0:56 | Chat: ask about someone not on record → "I don't have that on record" | **And it admits what it doesn't know.** | "When a fact isn't on record, it says so — and that honesty is what makes forgetting provable." |
| 7 | 0:56–1:18 | **HERO ①** Subjects → Forget & Prove → the erase animation | **One click. One ACID transaction.** | "Now the hero. One click erases a subject in a single ACID transaction — documents, graph nodes, edges, and vectors." |
| 8 | 1:18–1:40 | **HERO ②** the 3-part proof panel (zoom each in post) | **① existed — AS OF SYSTEM TIME · ② gone — re-search: 0 · ③ irreversible — crypto-shred** | "Then it proves it three ways. It existed — reconstructed with AS OF SYSTEM TIME. It's gone — a live vector and graph re-search returns nothing. And it's irreversible — the encryption key is crypto-shredded." |
| 9 | 1:40–1:52 | Certificate page → 🎙 **S3 object-lock console** (Object Lock: COMPLIANCE) | **Signed certificate → object-locked S3 (WORM).** | "Every erasure writes a signed certificate to object-locked S3 — write-once, tamper-proof." |
| 10 | 1:52–2:06 | `/verify`: paste cert → ✅ valid → edit one field → ❌ both fail | **Anyone can verify it. Tamper → it fails.** | "Anyone can re-check that certificate — re-derive the hash, verify the signature. Change a single field, and it fails." |
| 11 | 2:06–2:22 | 🎙 **MCP shot**: `cockroachdb-cloud` → `select count(*) … = 0` after the forget | **Verified through CockroachDB's own MCP server.** | "You don't even have to trust our app. Through CockroachDB's managed MCP server, an auditor queries the cluster directly — and confirms it's gone." |
| 12 | 2:22–2:32 | Graph: erased subject's nodes greyed; "1 shared kept" | **Shared memory is kept, not corrupted.** | "Erase one subject, and entities shared with a survivor are kept — never corrupted." |
| 13 | 2:32–2:42 | Stack / badges card | **C-SPANN · AS OF SYSTEM TIME · Serializable · Recursive CTE · Row-level TTL · Managed MCP · AWS S3 + EC2** | "Graph, vectors, time-travel, and proof — one CockroachDB store, live on AWS." |
| 14 | 2:42–2:52 | Landing + URL | **Try it live · 43-204-114-100.nip.io** | "Memory that forgets — completely, and provably. It's live. Try it yourself." |

## B. Read-ready VO (record one take; pause ~1s at each ⏸; ~140 wpm)

1. Every agentic-memory project this cycle answers one question — how do agents *remember* more? ⏸
2. Almost none ask the harder one — how do they *forget*? Completely, and provably. ⏸
3. This is Agent X. Verifiable forgetting for AI-agent memory, built on CockroachDB. ⏸
4. A knowledge graph, semantic recall, and a tamper-evident audit trail — in one transactional store. ⏸
5. It answers strictly from what it knows, and cites its sources. ⏸
6. When a fact isn't on record, it says so — and that honesty is what makes forgetting provable. ⏸
7. Now the hero. One click erases a subject in a single ACID transaction — documents, graph nodes, edges, and vectors. ⏸
8. Then it proves it three ways. It existed — reconstructed with AS OF SYSTEM TIME. It's gone — a live vector and graph re-search returns nothing. And it's irreversible — the encryption key is crypto-shredded. ⏸
9. Every erasure writes a signed certificate to object-locked S3 — write-once, tamper-proof. ⏸
10. Anyone can re-check that certificate — re-derive the hash, verify the signature. Change a single field, and it fails. ⏸
11. You don't even have to trust our app. Through CockroachDB's managed MCP server, an auditor queries the cluster directly — and confirms it's gone. ⏸
12. Erase one subject, and entities shared with a survivor are kept — never corrupted. ⏸
13. Graph, vectors, time-travel, and proof — one CockroachDB store, live on AWS. ⏸
14. Memory that forgets — completely, and provably. It's live. Try it yourself.

## C. What I capture (⚙️) vs. what you grab (🎙)
- ⚙️ **Me, automated (Playwright vs. live site, token pre-saved):** beats 1–8, 10, 12, 13, 14.
- 🎙 **You (needs your login — 2 short clips):**
  - Beat 9: your AWS S3 console showing the `agent-x-certs-vinayak` bucket object with **Object Lock: COMPLIANCE**.
  - Beat 11: an interactive Claude session using the `cockroachdb-cloud` MCP → run `select count(*) from nodes where 'X' = ANY(subjects)` and show it return **0** after a forget.
  - (Both are ~10-second screen recordings; ~2 min of your time.)

## D. Pipeline (from the runbook)
1. ⚙️ This storyboard.
2. 🎙 You record §B in one take → I `loudnorm` it.
3. ⚙️ faster-whisper word timestamps → anchor each beat.
4. ⚙️ Playwright one-shot capture per act (1600×900, dark, reset state between takes).
5. ⚙️ PNG/HTML caption overlays (no ffmpeg drawtext).
6. ⚙️ Rough cut (ffmpeg concat) → check flow → final comp → mux voice → `final.mp4` (1600×900, 30fps, H.264 + AAC 192k, hard cuts).
