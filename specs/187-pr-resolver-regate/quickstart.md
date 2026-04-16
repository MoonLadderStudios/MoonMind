# Quickstart: PR Resolver Child Re-Gating

## Focused Unit Validation

Run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py
```

Expected result:
- Resolver child request construction uses `publishMode = none`.
- Resolver child request construction uses `task.tool = {type: skill, name: pr-resolver, version: 1.0}`.
- Merge automation handles merged, already_merged, reenter_gate, manual_review, failed, missing disposition, and unsupported disposition cases.
- Re-gating after resolver remediation requires fresh readiness for the new head SHA.

## Full Unit Validation

Run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: the required unit suite passes before final MoonSpec verification.

## Hermetic Integration Validation

Run when Docker is available:

```bash
./tools/test_integration.sh
```

Expected result: hermetic integration tests pass. If Docker is unavailable inside the managed workspace, record the exact blocker in the final verification report.

## MoonSpec Verification

After implementation and tests, run the `/moonspec-verify` equivalent for:

```text
specs/187-pr-resolver-regate/spec.md
```

Confirm:
- MM-352 is preserved in source traceability and verification output.
- Each in-scope source design requirement maps to tests or implementation evidence.
- Resolver child launch and all allowed dispositions are covered.
