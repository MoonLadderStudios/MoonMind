# Quickstart: Explicit Failure and Rollback Controls

## Focused Unit Validation

1. Add failing unit tests for deployment failure classes:

```bash
pytest tests/unit/workflows/skills/test_deployment_update_execution.py -q
```

Expected initial failures should cover missing normalized failure-class metadata and incomplete failure-class matrix coverage.

2. Add failing API tests for rollback eligibility and explicit retry submission:

```bash
pytest tests/unit/api/routers/test_deployment_operations.py -q
```

Expected initial failures should cover missing recent action rollback eligibility, rollback metadata validation, and explicit second submission behavior.

3. Add focused UI tests for rollback visibility and confirmation:

```bash
./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx
```

During local frontend iteration after dependencies are prepared, this may also be run as:

```bash
npm run ui:test -- frontend/src/components/settings/OperationsSettingsSection.test.tsx
```

## Hermetic Integration Validation

Add or update integration coverage for the typed deployment update dispatch boundary:

```bash
pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q
```

The integration test should prove failed deployment execution exposes failure metadata and does not enqueue or perform rollback without explicit operator input.

When Docker is available for the required hermetic CI suite:

```bash
./tools/test_integration.sh
```

## Final Verification Commands

Run the full required unit suite from the managed-agent container:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run traceability checks:

```bash
rg -n "MM-523|DESIGN-REQ-001|rollbackEligibility|operationKind|failureClass" specs/265-explicit-failure-and-rollback-controls moonmind api_service frontend/src tests
```

## End-to-End Story Check

1. A failed deployment update produces `FAILED` or `PARTIALLY_VERIFIED`, a non-empty failure class/reason, and redacted artifact/audit refs.
2. The system does not automatically retry or roll back after failure.
3. Deployment stack state exposes rollback only for recent actions with trusted before-state evidence.
4. Rollback confirmation submits the same typed deployment update endpoint with a policy-valid previous image target and rollback metadata.
5. Recent actions show failure and rollback records with status, requested image, operator, reason, timestamps, run detail link, logs artifact link, and before/after summary.
6. Rollback requests without explicit confirmation fail closed before queueing a deployment update.
