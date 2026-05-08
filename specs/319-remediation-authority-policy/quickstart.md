# Quickstart: Remediation Authority Policy

## Focused Verification

Run the focused backend tests that cover MM-619 authority and policy behavior:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py
```

Run the focused Mission Control test that covers remediation authority/action policy submission:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Run the final required unit suite before completion:

```bash
./tools/test_unit.sh
```

Run traceability:

```bash
rg -n "MM-619|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-017" specs/319-remediation-authority-policy
```

## Expected Evidence

- Observe-only remediation denies side effects and supports dry-run/no-op output.
- Approval-gated remediation requires approval before side effects.
- Admin remediation records requester and effective privileged principal.
- High-risk actions require approval.
- Unsupported raw operations are not advertised and fail closed.
- Remediation decisions and audit outputs are redacted.
- MM-619 and source design IDs remain present in MoonSpec artifacts.
