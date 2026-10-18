---
name: document-update
description: Align a technical document with the actual code implementation and correct drift. Use when a user asks to update docs to match current behavior, audit a technical document against the repository, or fix stale architecture, API, workflow, or operator documentation.
metadata:
  required-capabilities:
    - git
---

# Document Update

Correct stale or incomplete explanations in the requested scope using the current checkout and the user's intended outcome.

## Procedure

1. Read repository instructions and identify the affected document or section. Search nearby documentation only as needed to find overlap or the relevant interface description.
2. Check consequential claims against code, tests, schemas, configuration, or real invocation paths. Distinguish factual drift from an implementation gap in an authorized design. Missing evidence means uncertain, not automatically stale.
3. Resolve conflicts against the current request and repository instructions. Do not turn buggy behavior into intended design or assume an old document overrules the task. Report genuine uncertainty instead of inventing a contract.
4. Rewrite affected text in place. Remove obsolete, duplicated, or obvious-from-code detail and retain useful operating knowledge and reasons. Repair affected links; leave accurate unrelated text alone. Broad work already authorized by the user does not need to be reduced to a plan.
5. Review accuracy and references. Documentation-only edits do not need tests for wording, headings, or structure. Follow behavior-testing instructions if code also changes, and report unrun checks honestly.

## Boundaries and result

Read only task-relevant sources; no mandatory architecture preread, metadata retrofit, or claim ledger is needed. Keep one-time plans, status, issue/PR references, and investigation output in issues, PRs, or gitignored artifacts. Preserve unrelated edits and redact secret-like material.

Report changed paths, corrected drift, evidence, and unresolved questions or verification gaps. If no edit is needed, say why. A blocked design decision can be reported directly with its evidence and next step; tracker creation is optional and requires authorization. Commit and publication also require authorization.
