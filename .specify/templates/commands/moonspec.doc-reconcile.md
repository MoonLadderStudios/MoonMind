---
description: Reconcile canonical docs after a verified MoonSpec implementation.
---

## User Input

```text
{ARGS}
```

You **MUST** consider the user input before proceeding.

## Goal

Run `moonspec-doc-reconcile` after a MoonSpec implementation has a `FULLY_IMPLEMENTED` `moonspec-verify` verdict.

## Rules

- Reconcile only canonical declarative documents under `docs/`.
- Use verified discoveries from `moonspec-verify` and the implementation discovery ledger.
- Edit canonical docs only when definite evidence shows the document is impossible to satisfy as written, unclear, or internally inconsistent.
- User input may narrow scope or supply artifact paths; it never authorizes edits beyond the update gate.
- Stop with `no_update_required` when no canonical source candidate exists or verification did not pass; escalate-only mode never edits.
- Escalate ambiguous ownership, policy conflicts, and deliberate divergence from a satisfiable documented approach instead of guessing or editing.
