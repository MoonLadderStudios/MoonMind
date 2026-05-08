# Quickstart: Remediation Action Contracts

**Traceability**: Jira issue `MM-620`; feature path `specs/320-remediation-action-contracts`.

## Prerequisites

- Python dependencies installed for the repository.
- Local unit test mode enabled for managed-agent containers: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- No external provider credentials are required for planned unit coverage.
- Hermetic integration checks require the normal MoonMind compose test environment when integration tests are added.

## Unit Test Strategy

Write or update focused tests before implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py -q
```

Required unit coverage:

1. Registry listing includes target type, input metadata, risk, preconditions, idempotency, verification, and audit metadata.
2. Action request evaluation validates action kind, input shape, authority, policy, risk, dry-run, idempotency, and fresh target evidence.
3. Published action request evidence contains the full v1 request contract and redacts sensitive values.
4. Published action result evidence contains status, message, applied timestamp when applicable, verification requirement, verification hint, before/after refs, and side effects.
5. Unsupported result statuses fail closed.
6. Unsupported raw operation classes are rejected before side effects.
7. High-risk actions return approval-required, rejected, or executable according to policy.

## Integration Test Strategy

If action execution artifact publication or service/activity wiring changes, add hermetic integration coverage marked `integration` and `integration_ci`, then run:

```bash
./tools/test_integration.sh
```

Required integration behavior when added:

1. Create a target execution and remediation execution with a linked context artifact.
2. List policy-compatible actions for the remediation task.
3. Evaluate one executable action request with authority and mutation guard decisions.
4. Execute through a fake owning action executor.
5. Read the published `remediation.action_request`, `remediation.action_result`, and `remediation.verification` artifacts.
6. Assert all v1 fields are present, redacted, bounded, and linked to the target workflow/run.
7. Assert unsupported raw action attempts do not create side-effect artifacts.

## End-to-End Story Validation

1. Start with a remediation context whose policy allows a supportable v1 action.
2. Confirm listed actions contain all required metadata and omit unsupported raw operations.
3. Request the action with a stable idempotency key and valid parameters.
4. Confirm request evidence is published before execution.
5. Return an allowed result status from the fake owning executor.
6. Confirm result and verification evidence are published with the v1 contract fields.
7. Repeat with high-risk, approval-required, unsupported, stale-target, duplicate-idempotency, and failed-result cases.

## Final Verification Commands

Before final MoonSpec verification, run the focused unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q
```

Run the full required unit suite before closing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run hermetic integration tests when integration coverage was added or service/activity boundaries changed:

```bash
./tools/test_integration.sh
```

Do not proceed to final verification until `plan.md`, `research.md`, `data-model.md`, this quickstart, and `contracts/remediation-action-contracts.md` remain consistent with `spec.md` and preserve `MM-620`.
