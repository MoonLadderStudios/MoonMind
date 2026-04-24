# Quickstart: Publish Durable DooD Observability Outputs

## Goal

Verify the current repository behavior against MM-504 before planning any production-code changes.

## Focused Unit Verification

Run the focused workload and artifact-related unit suites:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_task_runs.py
```

What this proves:
- Docker-backed workloads publish durable stdout, stderr, diagnostics, and declared output refs
- declared outputs and report-path handling remain bounded to MoonMind-owned artifact paths
- artifact and report publication metadata stay attached to workload results
- runtime artifact classes remain consistent with the existing report and artifact contract helpers
- task-run inspection surfaces can consume published workload artifacts

## Hermetic Integration Verification

Run the required hermetic integration suite if focused unit verification exposes drift or if fixes touch artifact publication, workload metadata, or task-run inspection boundaries:

```bash
./tools/test_integration.sh
```

What this proves:
- profile-backed and curated workload execution paths still publish inspectable artifacts through the trusted workload plane
- artifact lifecycle and task-run integration remain coherent end to end
- Docker-backed workload results remain inspectable without relying on daemon-local state

## Full Unit Verification

Before claiming MM-504 is complete, rerun the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## End-to-End Story Validation

1. Confirm `spec.md` preserves MM-504 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Review whether durable artifacts, declared outputs, and workload metadata satisfy the MM-504 acceptance scenarios.
4. If focused verification exposes drift, implement the smallest contract-preserving fix and rerun focused unit tests.
5. Run the full unit suite.
6. Escalate to `./tools/test_integration.sh` only if a fix touches artifact publication, workload metadata, or execution inspection boundaries.
7. Preserve MM-504 and DESIGN-REQ-021 through DESIGN-REQ-022 in downstream tasks and final verification artifacts.
