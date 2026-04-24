# Quickstart: Profile-Backed Workload Contracts

## Goal

Verify MM-500 using the existing profile-backed workload implementation plus the feature-local integration boundary.

## Focused Unit Verification

Run the workload contract and launcher-focused unit suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py
```

What this proves:

- approved profile-backed workload requests validate through runner profiles
- raw image, mount, device, and privilege fields are rejected
- bounded helper lifecycle behavior remains explicit
- disabled-mode denial is deterministic at the runtime boundary

## Hermetic Integration Verification

Run the hermetic integration suite after the MM-500 integration boundary is in place:

```bash
./tools/test_integration.sh
```

Focused integration coverage target:

- `tests/integration/temporal/test_profile_backed_workload_contract.py`
- existing curated-tool evidence in `tests/integration/temporal/test_integration_ci_tool_contract.py`

What this proves:

- the dispatcher/runtime boundary executes approved profile-backed workloads through a runner profile
- helper lifecycle remains bounded-service behavior at the integration layer
- raw container fields are rejected at the dispatcher/runtime boundary
- disabled mode denies the profile-backed tool path deterministically

## End-To-End Story Validation

1. Confirm `spec.md`, `plan.md`, `research.md`, `contracts/profile-backed-workload-contract.md`, `quickstart.md`, and `tasks.md` preserve MM-500 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Run the hermetic integration verification command.
4. Reuse the existing curated-tool evidence for `unreal.run_tests` and `moonmind.integration_ci`.
5. Complete final MoonSpec verification against MM-500, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-025.
