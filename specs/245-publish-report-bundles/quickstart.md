# Quickstart: Publish Report Bundles

## Goal

Verify the existing repository behavior against MM-493 before planning any production-code changes.

## Focused Unit Verification

Run the focused report bundle unit suites:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_report_workflow_rollout.py
```

What this proves:
- activity-owned report bundle publication is present
- workflow-visible bundle state stays compact and bounded
- final report bundles enforce exactly one canonical final marker
- execution and step linkage preserve bounded `step_id` and `attempt` metadata
- report-producing workflow families share the contract safely

## Contract And Mission Control Verification

Run the API contract regression and the focused task-detail UI suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

What this proves:
- latest canonical `report.primary` resolution remains server-defined
- Mission Control consumes the canonical report without browser-side guessing
- report presentation remains distinct from generic artifact browsing

## Full Unit Verification

Before claiming MM-493 is complete, rerun the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

Use the required hermetic integration suite only if verification exposes drift that requires changes to artifact persistence, activity publication boundaries, or API serialization/linkage:

```bash
./tools/test_integration.sh
```

For the current plan state, integration is explicit but contingent because the repository already contains focused unit, contract, and frontend evidence for the story.

## End-to-End Story Validation

1. Confirm `spec.md` preserves MM-493 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Run the contract and Mission Control verification command.
4. If verification exposes drift, implement the smallest contract-preserving fix and rerun focused tests.
5. Run the full unit suite.
6. Escalate to `./tools/test_integration.sh` only if a fix touches artifact persistence, activity publication, or API linkage.
7. Preserve MM-493 and DESIGN-REQ-005/006/012/013/019/020/021 in downstream tasks and verification artifacts.
