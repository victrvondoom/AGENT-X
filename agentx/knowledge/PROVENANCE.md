# Where `corpus.jsonl` came from

75 documents · ~115,000 characters · 5 sectors (airlines, banking, government,
healthcare, housing).

## Origin

The text was authored as Python string literals inside the seeding scripts of a
separate consumer-dispute project, and extracted from them into this corpus. The
extraction parsed those scripts with `ast` and never executed them.

Each document is an **authored summary of published regulation** — complaint
templates, escalation ladders, statutory timelines, published compensation bands
— attributed to the authority whose rules it describes (RBI, DGCA, IRDAI, TRAI,
RERA, UIDAI, MoHFW and others). It is secondary material describing public
regulation, not a reproduction of the regulators' own instruments.

## What this corpus is not

- **Not the regulators' official text.** A passage here paraphrases; it does not
  quote a gazette. Anything Agent X states from it is attributed to the passage,
  and `verify.py` marks a claim `verified` only against this text — which
  establishes that *this corpus* says so, not that the regulation does.
- **Not a legal authority.** `agentx/policy.py` decides entitlement; nothing here
  can. See the module docstring in `__init__.py` for the separation.
- **Not complete.** Telecom and e-commerce are absent. The source project's
  material for those sectors was a set of live web scrapers rather than authored
  content, so there was nothing to extract. They are reported as absent rather
  than padded — `GET /api/agentx/health` publishes the real per-sector counts.

## What was deliberately discarded

- **The pre-built vector indexes** (`datasets/vector_embeddings/*.jsonl`) were
  Git LFS pointer stubs — 131-byte metadata files, no content. Nothing was
  recoverable from them and nothing was inferred.
- **The application fixtures** (`datasets/app_store/*.jsonl`) were test data
  containing placeholder text and absolute paths into another developer's
  temporary directory. They describe no regulation and were not imported.
- **Artificial padding.** Several source documents were inflated by repeating
  their own body (`content * 5`). The repetition carries no additional content
  and would have skewed retrieval scoring toward the padded documents, so the
  base text was taken exactly once.

## Regenerating or extending

Retrieval is deterministic BM25 with a relevance floor
(`retrieve.MIN_MATCHED_MASS`) calibrated against **this** corpus. IDF scales with
log(corpus size), so adding documents shifts every score.
`tests/test_agentx_knowledge.py::test_retrieval_calibration_holds_both_directions`
pins the separation from both sides and will fail if a change collapses the gap
between queries the corpus covers and queries it does not. Re-check that
threshold whenever the corpus changes; do not simply move it to make a test pass.
