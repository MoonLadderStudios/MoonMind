# Quickstart: Remediation Mutation Guards

Validate MM-455 in runtime mode. The implementation must prevent conflicting, duplicate, repeated, nested, or stale remediation mutations before any side-effecting action executes.

## Targeted Red-First Tests

Run the focused remediation test module while iterating:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Expected red-first coverage before implementation:

- concurrent mutation attempts allow only one exclusive `target_execution` lock holder;
- idempotent lock acquisition returns the same decision for the same holder and target;
- stale locks recover explicitly, while lost locks block silent continued mutation;
- duplicate action requests return the canonical action-ledger result;
- unsafe idempotency-key reuse is denied;
- action budgets, per-kind attempt limits, and cooldowns deny or escalate repeated actions;
- self-targeting and automatic nested remediation are denied by default;
- target run/state/summary/session changes produce no-op, re-diagnosis, or escalation decisions;
- guard outputs redact secret-like values, presigned URLs, storage keys, and absolute local paths.

## Full Unit Verification

Before finalizing, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Verification

No compose-backed integration test is required for this story because the guard is a local remediation service boundary with no external providers or credentials. The service-boundary tests use the async database fixture and existing remediation execution/link setup.

## End-to-End Story Check

1. Create or load a target execution and a linked remediation execution.
2. Prepare a side-effecting remediation action request with fresh target health.
3. Evaluate mutation guards before action execution.
4. Confirm the result is executable only when lock, ledger, budget, cooldown, nested-remediation, and target-freshness checks all pass.
5. Confirm every non-executable result includes a bounded reason and redaction-safe serialized payload.
