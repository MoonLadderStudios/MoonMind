# Quickstart: Enforce Docker Workflow Modes and Registry Gating

## Goal

Implement and verify the MM-499 workflow Docker mode contract before generating tasks or claiming runtime completion.

## Focused Unit Verification

Run the focused settings and workload policy suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/config/test_settings.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_workload_run_activity.py
```

What this proves:
- the canonical workflow Docker mode setting normalizes correctly
- invalid mode values fail fast
- worker/runtime registration uses the selected mode
- disabled-, profiles-, and unrestricted-mode execution behavior is covered at the tool handler and activity boundaries

## Hermetic Integration Verification

Run the required hermetic integration suite when the mode-aware registration/dispatch boundary changes:

```bash
./tools/test_integration.sh
```

Focused integration coverage target:
- `tests/integration/temporal/test_integration_ci_tool_contract.py`
- add or update one `integration_ci` boundary test that proves registry exposure and dispatch behavior stay aligned for the selected workflow Docker mode

What this proves:
- the worker-facing dispatcher exposes the expected Docker-backed tools for the selected mode
- forbidden Docker-backed tools remain unavailable or denied through the real integration boundary
- curated integration-ci routing still works in the allowed modes

## Full Unit Verification

Before claiming MM-499 is complete, rerun the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## End-to-End Story Validation

1. Confirm `spec.md`, `plan.md`, and downstream artifacts preserve MM-499 and the original Jira preset brief.
2. Add unit tests for mode normalization and mode-aware registration/denial behavior before changing production code.
3. Implement the canonical `MOONMIND_WORKFLOW_DOCKER_MODE` contract and remove the superseded boolean workflow Docker setting.
4. Rerun the focused unit verification command.
5. Rerun hermetic integration verification if worker/runtime registration or dispatcher behavior changed.
6. Run the full unit suite.
7. Preserve MM-499 and DESIGN-REQ-001/003/007/008/009/010/011 in tasks and final verification output.
