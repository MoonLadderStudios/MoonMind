# Quickstart: Report Artifact Contract

## Goal

Verify the existing repository behavior against MM-492 before planning any production-code changes.

## Unit Test Strategy

Run the focused unit suites that cover report metadata validation, bundle validation/publication, and rollout mapping behavior:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_report_workflow_rollout.py
```

What this proves:
- report link semantics and generic fallback behavior
- safe/bounded report metadata validation
- compact `report_bundle_v = 1` validation
- activity/service-side report bundle publication behavior
- report/evidence/observability separation for workflow mappings

## Contract And UI Verification

Run the API contract regression and the focused task-detail UI suite that consumes canonical `report.primary` artifacts:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

What this proves:
- latest canonical report lookup remains server/link-driven
- Mission Control consumes canonical report artifacts without local guessing
- related report content stays distinct from generic artifact presentation

## Full Unit Suite

Before claiming the story is complete, rerun the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

Use the required hermetic integration suite when any implementation change touches artifact persistence, activity boundaries, API serialization, or execution-scoped artifact linkage:

```bash
./tools/test_integration.sh
```

For the current plan state, integration is a contingency rather than a guaranteed code-change requirement because the repo already contains focused unit and contract evidence for the MM-492 contract.

## End-to-End Story Validation

1. Confirm `spec.md` preserves MM-492 and the original Jira preset brief.
2. Run the focused unit suites.
3. Run the API contract/UI verification command.
4. If any focused verification fails, implement the smallest contract-preserving fix and rerun focused tests.
5. Run the full unit suite.
6. Escalate to `./tools/test_integration.sh` if the fix touched artifact persistence, activity publication, or API serialization/linkage.
7. Record final traceability and evidence in the later verification artifact.
