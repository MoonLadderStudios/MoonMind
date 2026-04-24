# Quickstart: Shared Docker Workload Execution Plane

## Goal

Verify MM-503 using the existing Docker-backed workload implementation, then harden only the gaps exposed by cross-class shared-plane verification.

## Focused Unit Verification

Run the workload contract, launcher, and Temporal activity unit suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_activity_runtime.py
```

What this proves:

- Docker-backed tool definitions route through `mm.tool.execute` with `docker_workload` capability
- ownership labels and workload metadata are derived deterministically for structured and unrestricted requests
- launcher timeout, artifact publication, and cleanup primitives remain bounded
- mode-aware routing and activity-runtime enforcement stay deterministic

## Hermetic Integration Verification

Run the hermetic integration suite after adding MM-503-focused cross-class coverage:

```bash
./tools/test_integration.sh
```

Focused integration coverage target:

- existing dispatcher/runtime coverage in `tests/integration/temporal/test_profile_backed_workload_contract.py`
- a planned MM-503-focused extension covering unrestricted container and Docker CLI execution-path verification, plus any missing metadata or cleanup assertions discovered during unit analysis

What this proves:

- profile-backed and helper launches already use the shared workload plane
- unrestricted container and Docker CLI paths, when enabled, share the same observable execution contract
- timeout, cancellation, and cleanup behavior remain bounded across supported launch classes
- metadata preserves task/run identity and workload access class consistently across the shared execution plane

## End-To-End Story Validation

1. Confirm `spec.md`, `plan.md`, `research.md`, `contracts/shared-docker-workload-plane-contract.md`, and `quickstart.md` preserve MM-503 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Add or confirm MM-503-specific integration assertions for unrestricted and structured launch classes.
4. Run the hermetic integration verification command.
5. Review metadata and cleanup evidence for runtime mode/access-class completeness and ownership boundaries.
6. Run `/moonspec.verify` for `specs/252-route-docker-workload-plane/` and complete final verification against MM-503, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, DESIGN-REQ-024.
