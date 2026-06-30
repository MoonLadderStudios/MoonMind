---
description: Reconcile canonical docs after a verified MoonSpec implementation.
---
<!-- Generated from moonspec/bundle/commands/markdown/moonspec.doc-reconcile.md; edit MoonSpec repo instead. -->


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
- Edit canonical docs only when evidence definitely shows the document is functionally wrong, internally inconsistent, or intentionally superseded by verified best practice.
- Stop with `no_update_required` when no canonical source candidate exists or verification did not pass.
- Escalate ambiguous ownership or policy conflicts instead of guessing.
