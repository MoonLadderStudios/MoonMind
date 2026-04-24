# Quickstart: Workspace, Mount, and Session-Boundary Isolation

## Goal

Verify MM-502 using the existing Docker-backed workload implementation plus explicit dispatcher/runtime proof for session-assisted workload isolation.

## Focused Unit Verification

Run the workload contract and runtime-focused unit suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py
```

What this proves:

- workspace-root validation rejects repo, artifact, scratch, and declared output escapes
- session association metadata requires `sessionId` and remains bounded metadata
- implicit auth-like mounts are rejected unless explicit credential policy is declared
- runtime mode denial remains deterministic at the activity boundary

## Hermetic Integration Verification

Run the hermetic integration suite after the MM-502 boundary proof is in place:

```bash
./tools/test_integration.sh
```

Focused integration coverage target:

- existing dispatcher/runtime coverage in `tests/integration/temporal/test_profile_backed_workload_contract.py`
- a planned MM-502-focused dispatcher/runtime isolation test covering session-associated workload metadata and policy alignment

What this proves:

- the dispatcher/runtime boundary preserves workload identity for session-assisted launches
- session-associated workload results carry bounded `sessionContext` metadata only
- policy enforcement at the integration layer matches the same workload isolation rules already enforced by unit-tested request validation

## End-To-End Story Validation

1. Confirm `spec.md`, `plan.md`, `research.md`, `contracts/workload-isolation-contract.md`, and `quickstart.md` preserve MM-502 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Run the hermetic integration verification command.
4. Confirm session-associated workload results remain workload artifacts rather than session continuity artifacts.
5. Complete final MoonSpec verification against MM-502, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022.
