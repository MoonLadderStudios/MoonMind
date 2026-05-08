# Quickstart: Remediation Lock, Ledger, and Loop Guards

## Scope

This quickstart validates MM-621 at the plan boundary. Existing evidence indicates no new implementation is planned; verification should prove the current remediation mutation guard still satisfies the spec.

## Setup

From the repository root:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

The standard plan setup helper is not usable on this managed branch because it expects a numeric feature branch name. The active feature directory is instead resolved from `.specify/feature.json` as `specs/321-remediation-lock-ledger-guards`.

## Unit Test Strategy

Run the focused unit coverage first:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

The focused unit suite must cover:

- exclusive target mutation lock acquisition and conflict,
- duplicate lock/idempotency replay,
- durable lock and ledger hydration across service restart,
- released lock loss,
- action ledger duplicate and unsafe reuse,
- retry budgets and cooldowns,
- nested remediation and self-target denial,
- target freshness unavailable/material-change decisions,
- redaction in serialized guard results.

Before closing the implementation workflow, run the full required unit suite:

```bash
./tools/test_unit.sh
```

## Integration Test Strategy

Run the focused hermetic integration coverage when iterating on the action evidence boundary:

```bash
pytest tests/integration/temporal/test_remediation_action_contracts.py -m 'integration_ci' -q --tb=short
```

Before final closure, run the required hermetic integration wrapper:

```bash
./tools/test_integration.sh
```

The integration path must verify that remediation context, authority decisions, mutation guard decisions, action request/result artifacts, and verification artifacts compose without external credentials.

## End-to-End Story Validation

1. Create or simulate a remediation task linked to a target execution.
2. Evaluate a side-effecting action with a stable idempotency key and fresh target health.
3. Verify exactly one remediator can hold the target mutation lock.
4. Repeat the same logical request and verify the ledger-backed prior decision is returned.
5. Submit a competing holder, changed target, exhausted budget, cooldown repeat, missing freshness, self-target, and nested remediation case.
6. Confirm each blocked path returns a bounded reason and `executable=false`.
7. Confirm action evidence publication only proceeds for allowed executable decisions.

## Expected Outcome

- No duplicated side effects are allowed by guard evaluation.
- All blocked mutation paths produce bounded operator-visible reasons.
- MM-621 remains traceable in `spec.md`, `plan.md`, design artifacts, and final verification evidence.
