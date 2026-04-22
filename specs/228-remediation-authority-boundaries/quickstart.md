# Quickstart: Remediation Authority Boundaries

## Intent

Validate MM-453 in runtime mode. The implementation must enforce remediation authority modes, permissions, security profiles, high-risk approvals, idempotency, audit output, and redaction boundaries before any side-effecting remediation action is considered executable.

## Focused Unit Tests

Run the focused remediation test file:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Expected coverage:

- `observe_only` allows diagnosis/dry-run style decisions but rejects side-effecting execution.
- `approval_gated` requires recorded approval before side-effecting execution.
- `admin_auto` can allow policy/profile-permitted low and medium risk actions.
- High-risk actions require approval or are denied according to policy even under `admin_auto`.
- Target view permission alone cannot request admin profile use, approve high-risk actions, or inspect privileged audit output.
- Action decisions include redacted audit payloads with requestor and execution principal.
- Duplicate idempotency keys return the same decision and avoid duplicate executable outcomes.
- Raw host shell, Docker daemon, SQL/database, storage-key, local-path, and raw-secret access are denied or redacted.

## Full Unit Suite

Before finalizing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

No compose-backed integration is expected for this slice. The service-boundary test should create a target execution, create a remediation execution with authority policy, build context, prepare a side-effecting action request, and evaluate the action authority decision through local fixtures.

## Manual Verification

1. Confirm `specs/228-remediation-authority-boundaries/spec.md` preserves MM-453 and `DESIGN-REQ-010`, `DESIGN-REQ-011`, and `DESIGN-REQ-024`.
2. Confirm action decisions never expose raw secrets, storage keys, presigned URLs, absolute local paths, raw host shell commands, Docker daemon access, or SQL/database edit access.
3. Confirm final verification reports MM-453 traceability and runtime-mode behavior.
