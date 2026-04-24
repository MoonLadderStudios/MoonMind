# Quickstart: Unrestricted Container and Docker CLI Contracts

## Goal

Verify MM-501 using the existing unrestricted execution implementation plus the feature-local contract and traceability review.

## Focused Unit Verification

Run the unrestricted-request and workload-policy unit suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/config/test_settings.py
```

What this proves:

- unrestricted container requests parse and validate through the structured contract
- unrestricted Docker CLI requests require `command[0] == docker`
- unrestricted tools are only exposed in `unrestricted` workflow Docker mode
- runtime invocation denies unrestricted tools in `profiles` mode
- unrestricted launcher behavior stays bounded and preserves Docker-specific execution

## Hermetic Integration Verification

Run the hermetic integration suite after the focused unit pass:

```bash
./tools/test_integration.sh
```

Focused integration coverage target:

- `tests/integration/temporal/test_integration_ci_tool_contract.py`

What this proves:

- the dispatcher/runtime boundary omits unrestricted tools outside `unrestricted` mode
- the dispatcher/runtime boundary executes `container.run_container` in `unrestricted` mode
- mode-aware registration and runtime denial stay aligned for the unrestricted tool surface

## End-To-End Story Validation

1. Confirm `spec.md`, `plan.md`, `research.md`, `contracts/unrestricted-docker-workload-contract.md`, and `quickstart.md` preserve MM-501 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Run the hermetic integration verification command.
4. Compare the unrestricted example flows in `docs/ManagedAgents/DockerOutOfDocker.md` sections 18.2-18.4 against the current unrestricted request schema and launcher behavior.
5. Complete final MoonSpec verification against MM-501, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-022, DESIGN-REQ-025.
