---
name: citation-substance-review
description: Use when asked to judge whether citations substantively support the claims, methods, definitions, evidence, or quotations they are invoked for in a research document. This skill complements citation-verification-protocol.md by giving the assistant a careful relevance-review rubric; it is not a replacement for the protocol gates.
metadata:
  short-description: Judge citation relevance against invocation context
---

# Citation Substance Review

Use this skill for the judgement step in a citation audit: deciding whether a
source actually supports the claim it is attached to.

## Core Rule

The canonical workflow is `agent-protocols/citation-verification-protocol.md`.
This skill is an adapter for assistant behaviour. Use it to perform and record
the scholarly judgement that deterministic scripts cannot make by themselves.

Do not mark a citation `verified` merely because the source exists, has correct
metadata, or is broadly related to the topic. Verify the invocation context.

## Review Loop

For each cited source:

1. Read the local manuscript passage around the citation.
2. State the smallest claim the citation is carrying.
3. Inspect the source at the most precise available locator: page, section,
   table, figure, abstract, dataset field, disclosure line, or official page.
4. Classify the relationship using the support types from the protocol:
   `direct_support`, `contextual_support`, `method_support`,
   `partial_support`, `misplaced`, `unsupported`, `blocked`, or
   `not_applicable`.
5. Record the judgement in `docs/temp/citation-content-support.toml` with:
   `status`, `note`, `support_type`, `claim_context`, `source_locator`,
   `evidence_note`, and `risk`.
6. Leave the row non-passing when the manuscript overstates what the source can
   carry. Fix the prose, move the citation, replace the source, or mark the row
   blocked before claiming the gate is green.

## Passing Judgements

Use `status = "verified"` only when the support type is one of:

- `direct_support`
- `contextual_support`
- `method_support`

Use `status = "not_applicable"` only when the bibliography entry is present but
no substantive claim invokes it. Explain why.

For `partial_support`, `misplaced`, `unsupported`, or `blocked`, keep the row
non-passing until the manuscript or source record is corrected.

## Evidence Notes

Write notes as compact audit evidence, not prose praise. A good note says what
the manuscript asked the source to prove and what the source actually says.

Example:

```toml
[example2026source]
status = "verified"
note = "Used for the 2030 data-centre electricity projection under the central scenario."
support_type = "direct_support"
claim_context = "The manuscript cites this source for a 2030 data-centre electricity projection."
source_locator = "Section 2.1, table 3"
evidence_note = "The source gives the same projection and scenario boundary."
risk = "low"
```

## Blocking Conditions

Mark the row `blocked` or otherwise non-passing when:

- the source cannot be accessed;
- the locator is too vague to verify the claim;
- the source supports a weaker, narrower, or different claim;
- the citation is being used for causality when the source shows correlation;
- timing, jurisdiction, population, workload, units, or denominator differ;
- the source is an opinion, forecast, or secondary account but the manuscript
  treats it as observed evidence;
- a quotation cannot be found verbatim.

When in doubt, make the uncertainty visible in the ledger and leave the gate
red.
