# Quickstart: Jules Provider Adapter Runtime Alignment

## Goal

Validate that MoonMind dispatches standard Jules work as one bundled execution, preserves clarification-only follow-up messaging, and reports branch publication truthfully.

## Prerequisites

- Jules runtime is configured in the local/dev environment.
- The feature branch `105-jules-provider-adapter` is checked out.
- Unit-test dependencies are installed.

## Validation Steps

1. Run the unit/workflow suite with the canonical script:

```bash
./tools/test_unit.sh
```

2. Confirm updated workflow-boundary coverage proves:
   - consecutive Jules plan nodes are bundled into one execution,
   - normal bundled execution does not use `integration.jules.send_message`,
   - clarification/auto-answer flows can still use `integration.jules.send_message`,
   - branch publication succeeds only after merge verification,
   - branch publication failure scenarios report non-success outcomes.

3. If a real-provider smoke test is needed, run it only against a scratch repository and verify:
   - one provider session is created for the bundled work,
   - the final result exposes bundle metadata,
   - `publishMode: "branch"` only reports success when the requested target branch actually receives the changes.

## Expected Outcome

- No normal Jules multi-step session chaining remains for new executions.
- Bundle/result metadata is visible in workflow results.
- Branch publication semantics are truthful in both success and failure paths.
