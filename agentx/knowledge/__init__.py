"""
The research layer — regulatory guidance Agent X can retrieve, cite, and check.

`policy.py` answers *what is this person entitled to*, deterministically, from a
small corpus of declarative rules. It is deliberately narrow: a rule only earns a
place there if its conditions can be evaluated against the case fact graph without
a model. That narrowness is the point, and it is also a gap — when no declared
policy covers a situation, Agent X previously had nothing to say beyond "unknown".

This package fills that gap without weakening it. It holds long-form regulatory
guidance (complaint routes, escalation ladders, statutory timelines, the published
compensation bands) as retrievable text, and it is strictly subordinate to
`policy.py`:

    policy.py    DECIDES entitlement. Deterministic conditions over facts.
    knowledge/   INFORMS the user and grounds the letter. Retrieved text, cited.

A retrieved passage never establishes an entitlement and never sets a number the
governor acts on. It supplies the citation and the procedural detail — which
ombudsman, within how many days, addressed to whom — that a correct entitlement
still needs in order to be actionable.

Everything here is deterministic: BM25 over a local corpus, no network, no model,
no database. It runs under `use_llm=False` exactly as it runs in production, which
is what lets `evals/` reproduce it.
"""
from __future__ import annotations

from agentx.knowledge.corpus import Passage, sectors, stats
from agentx.knowledge.retrieve import search, sectors_for_domain
from agentx.knowledge.verify import (VERDICTS, CitationCheck, verify_citation,
                                     verify_citations)

__all__ = ["Passage", "sectors", "stats", "search", "sectors_for_domain",
           "VERDICTS", "CitationCheck", "verify_citation", "verify_citations"]
