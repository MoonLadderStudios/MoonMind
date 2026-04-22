# Research: Workflow Docker Access Setting

## FR-001 / FR-002 / SC-001

Decision: Implemented verified; `WorkflowSettings` exposes `workflow_docker_enabled` with validation alias `MOONMIND_WORKFLOW_DOCKER_ENABLED` and default `True`.
Evidence: `moonmind/config/settings.py` defines the setting. `tests/unit/config/test_settings.py` covers the default and env override behavior.
Rationale: MM-476 requires one app-level setting with default enabled.
Alternatives considered: Using an untyped `os.environ` check at each call site; rejected because settings are the repo pattern for app-level runtime configuration.
Test implications: Unit tests for default and env override.

## FR-003 / FR-006 / SC-003

Decision: Implemented verified; existing DooD tool routing and activity routing remain available when enabled, while Docker-backed workflow entry points are gated.
Evidence: `moonmind/workloads/tool_bridge.py` registers generic and curated DooD skill handlers with the setting passed into handler construction. `moonmind/workflows/temporal/activity_runtime.py` gates direct `workload.run` activity calls. `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_integration_ci_tool_contract.py` cover enabled routing and boundary behavior.
Rationale: The story gates the existing boundary; it should not replace it.
Alternatives considered: Routing all Docker tools through a new activity; rejected because it would duplicate the existing DooD substrate.
Test implications: Unit tests cover handler behavior; integration-boundary tests cover dispatcher routing without requiring a live Docker daemon.

## FR-004 / FR-005 / SC-002

Decision: Implemented verified; fail-fast policy checks run before runner-profile validation or launcher invocation in generic workload tool handlers and direct `workload.run` activity.
Evidence: `moonmind/workloads/tool_bridge.py` denies disabled Docker-backed tool calls with `docker_workflows_disabled` before request construction and launch. `moonmind/workflows/temporal/activity_runtime.py` denies direct activity calls before registry or launcher access. Unit tests use failing registry and launcher fakes to prove those downstream calls do not happen when disabled.
Rationale: Denial must prevent Docker side effects and produce deterministic `docker_workflows_disabled` evidence.
Alternatives considered: Checking after validation; rejected because the requirement says disabled requests fail before Docker workload routing.
Test implications: Unit tests with fake registry/launcher that fail if called while disabled.

## FR-007 / SC-005

Decision: Implemented verified; managed-session boundaries are preserved and the setting does not alter normal session container mounts.
Evidence: The implementation changes are limited to workflow settings, workload tool bridge, Temporal activity runtime, worker wiring, and workload runner profiles. Managed-session launch code remains separate, and the full unit suite passed with existing managed-session coverage.
Rationale: MM-476 explicitly separates workflow Docker tooling from raw socket access in normal agent/session containers.
Alternatives considered: Passing Docker settings into managed-session launch; rejected as out of scope and unsafe.
Test implications: Existing managed-session tests plus final code inspection evidence.

## FR-008 / FR-009 / SC-004

Decision: Implemented verified; `moonmind.integration_ci` is a curated DooD tool that maps to `./tools/test_integration.sh`, the existing workload result contract, and a dedicated runner profile.
Evidence: `moonmind/workloads/tool_bridge.py` defines the curated tool and maps requests to runner profile `moonmind-integration-ci` with command `./tools/test_integration.sh`. `config/workloads/default-runner-profiles.yaml` includes the dedicated profile. `tests/unit/workloads/test_workload_tool_bridge.py` and `tests/integration/temporal/test_integration_ci_tool_contract.py` assert tool definition, request mapping, and artifact-backed result fields. Failure context emitted by the runner is preserved through the existing `diagnosticsRef` and `outputRefs` contract rather than a new compose-log-specific field.
Rationale: The brief requests an approved integration-test tool/activity without changing the human/GitHub Actions script path.
Alternatives considered: Asking users to call `container.run_workload` directly with command `./tools/test_integration.sh`; rejected because the requirement asks for a curated integration-test tool.
Test implications: Unit tests for tool definition, request mapping, result shape, and default profile loading. A lightweight integration contract test can route the curated tool through dispatcher semantics without needing a live Docker daemon.

## FR-010

Decision: Implemented verified; do not change `./tools/test_integration.sh`.
Evidence: The story can invoke the existing script through a curated tool while preserving direct script usage.
Rationale: Human and GitHub Actions usage should remain stable.
Alternatives considered: Wrapping or rewriting the script; rejected as unnecessary and outside scope.
Test implications: Final diff/verification confirms no script changes.

## FR-011 / SC-006

Decision: Implemented verified; MM-476 is preserved in source input, active spec artifacts, and verification evidence.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-476-moonspec-orchestration-input.md`, `specs/237-workflow-docker-access/spec.md`, `plan.md`, `tasks.md`, and verification checks include MM-476.
Rationale: Downstream PR and verification traceability depends on stable Jira issue references.
Alternatives considered: None.
Test implications: Final verification and `rg` traceability check.
