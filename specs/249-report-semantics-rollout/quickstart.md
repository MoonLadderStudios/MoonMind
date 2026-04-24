# Quickstart: Report Semantics Rollout

## Goal

Verify the existing repository behavior against MM-497 before planning any production-code changes.

## Focused Unit Verification

Run the focused rollout and artifact unit suites plus the execution-detail router tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_executions.py --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

What this proves:
- existing generic outputs remain distinct from report-producing workflows
- report-producing workflows require explicit `report.primary` semantics
- representative unit-test, coverage, pentest/security, and benchmark mappings remain valid
- execution detail continues to surface canonical report behavior correctly
- Mission Control continues to consume canonical reports without browser-side guessing

## Contract Verification

Run the focused execution API contract suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py
```

What this proves:
- explicit report projections remain artifact-backed and server-defined
- canonical report semantics continue to flow through the execution API boundary

## Full Unit Verification

Before claiming MM-497 is complete, rerun the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

Use the required hermetic integration suite only if focused verification reveals drift that requires changes to artifact persistence, runtime publication boundaries, or compose-backed API serialization:

```bash
./tools/test_integration.sh
```

For the current plan state, integration is explicit but contingent because the repository already contains focused runtime, contract, and UI evidence for the story.

## End-to-End Story Validation

1. Confirm `spec.md`, `plan.md`, and later `tasks.md` / `verification.md` preserve MM-497 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Run the focused contract verification command.
4. If verification exposes drift, implement the smallest contract-preserving fix and rerun focused tests.
5. Run the full unit suite.
6. Escalate to `./tools/test_integration.sh` only if a fix crosses persistence, activity publication, or API serialization boundaries.
7. Preserve MM-497 and DESIGN-REQ-021/023/024 in downstream tasks and verification artifacts.
