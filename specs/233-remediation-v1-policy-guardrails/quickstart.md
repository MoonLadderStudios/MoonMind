# Quickstart: Remediation V1 Policy Guardrails

## Scope

Verify MM-458 runtime guardrails:
- remediation v1 remains manual by default;
- future self-healing policy metadata is inert unless explicitly supported and bounded;
- raw administrative capabilities are absent or fail closed;
- documented edge cases produce bounded outcomes.

## Test Commands

Targeted verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py
```

Final unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## End-To-End Scenario

1. Create a normal MoonMind.Run target.
2. Create another MoonMind.Run with only future `task.remediationPolicy` metadata.
3. Confirm no `TemporalExecutionRemediationLink` is created for the policy-only run.
4. Confirm the same metadata remains ordinary task parameters and does not automatically spawn an admin healer.
5. Ask remediation action authority for allowed admin actions.
6. Confirm allowed action metadata contains typed actions only and no raw host, Docker, SQL, storage, secret-read, or redaction-bypass actions.
7. Run existing remediation action/context tests to confirm raw action requests, degraded evidence, lock conflicts, stale target checks, and redaction-safe outputs remain bounded.

## Expected Result

All tests pass, and final verification can trace MM-458 plus DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-024 through the spec, plan, tasks, tests, and implementation evidence.
