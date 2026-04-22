# Research: Workflow Docker Access Setting

## FR-001 / FR-002 / SC-001

Decision: Missing; add `workflow_docker_enabled` to `WorkflowSettings` with validation alias `MOONMIND_WORKFLOW_DOCKER_ENABLED` and default `True`.
Evidence: `moonmind/config/settings.py` has workflow settings for skills, live sessions, artifacts, and Docker binary/network inputs, but no setting that gates workflow Docker workload execution. `tests/unit/config/test_settings.py` covers workflow setting defaults and env overrides.
Rationale: MM-476 requires one app-level setting with default enabled.
Alternatives considered: Using an untyped `os.environ` check at each call site; rejected because settings are the repo pattern for app-level runtime configuration.
Test implications: Unit tests for default and env override.

## FR-003 / FR-006 / SC-003

Decision: Implemented unverified; existing DooD tool routing and activity routing should remain unchanged when enabled.
Evidence: `moonmind/workloads/tool_bridge.py` registers generic DooD skill handlers. `moonmind/workflows/temporal/activity_runtime.py` exposes `workload.run`. `tests/unit/workflows/temporal/workflows/test_run_integration.py` proves DooD skill steps route to `mm.activity.agent_runtime`.
Rationale: The story gates the existing boundary; it should not replace it.
Alternatives considered: Routing all Docker tools through a new activity; rejected because it would duplicate the existing DooD substrate.
Test implications: Existing integration boundary remains evidence; add enabled-path unit tests to avoid accidental denial.

## FR-004 / FR-005 / SC-002

Decision: Missing; add fail-fast policy checks before runner-profile validation or launcher invocation in both generic workload tool handlers and direct `workload.run` activity.
Evidence: Current `build_workload_tool_handler` validates and launches without a workflow-level gate. Current `TemporalAgentRuntimeActivities.workload_run` validates and launches without a gate.
Rationale: Denial must prevent Docker side effects and produce deterministic `docker_workflows_disabled` evidence.
Alternatives considered: Checking after validation; rejected because the requirement says disabled requests fail before Docker workload routing.
Test implications: Unit tests with fake registry/launcher that fail if called while disabled.

## FR-007 / SC-005

Decision: Implemented unverified; preserve managed-session boundaries and add code/test evidence that the new setting does not alter normal session container mounts.
Evidence: `DockerCodexManagedSessionController` builds session containers separately from workload tools, and existing tests assert controlled run args.
Rationale: MM-476 explicitly separates workflow Docker tooling from raw socket access in normal agent/session containers.
Alternatives considered: Passing Docker settings into managed-session launch; rejected as out of scope and unsafe.
Test implications: Existing managed-session tests plus final code inspection evidence.

## FR-008 / FR-009 / SC-004

Decision: Missing; add `moonmind.integration_ci` as a curated DooD tool that maps to `./tools/test_integration.sh`, the existing workload result contract, and a dedicated runner profile.
Evidence: `build_dood_tool_definition_payload` already defines generic and curated DooD tools. `config/workloads/default-runner-profiles.yaml` currently contains `unreal-5_3-linux` only. `WorkloadResult` already carries stdout, stderr, diagnostics, output refs, and metadata.
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

Decision: Implemented unverified; preserve MM-476 in all artifacts and final evidence.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-476-moonspec-orchestration-input.md` and `spec.md` include MM-476.
Rationale: Downstream PR and verification traceability depends on stable Jira issue references.
Alternatives considered: None.
Test implications: Final verification and `rg` traceability check.
