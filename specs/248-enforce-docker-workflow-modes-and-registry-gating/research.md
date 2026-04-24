# Research: Enforce Docker Workflow Modes and Registry Gating

## Story Classification

Decision: Treat MM-499 as a single-story runtime implementation request, not a breakdown candidate.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-499-moonspec-orchestration-input.md`; `specs/248-enforce-docker-workflow-modes-and-registry-gating/spec.md`.
Rationale: The Jira preset brief defines one independently testable runtime outcome: one deployment-owned workflow Docker mode governs tool exposure and runtime denial behavior.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one story and does not require processing multiple specs.
Test implications: Unit tests plus at least one hermetic integration boundary are required because the story changes worker/runtime policy wiring.

## FR-001 / DESIGN-REQ-001 Deployment-Owned Workflow Docker Policy

Decision: partial.
Evidence: `moonmind/config/settings.py` defines `workflow_docker_enabled`; `moonmind/workflows/temporal/worker_runtime.py` passes the boolean into tool registration and activity runtime; `moonmind/workloads/tool_bridge.py` and `moonmind/workflows/temporal/activity_runtime.py` deny execution when the boolean is false.
Rationale: Deployment-owned gating exists, but it is only a boolean on/off switch and does not satisfy the required tri-mode policy model.
Alternatives considered: Treat the existing boolean gate as sufficient. Rejected because MM-499 explicitly requires `disabled`, `profiles`, and `unrestricted` modes with different discovery and execution semantics.
Test implications: Unit tests for settings normalization plus worker/runtime policy wiring; integration test to confirm the selected mode reaches actual tool dispatch behavior.

## FR-002 / FR-003 / DESIGN-REQ-003 / DESIGN-REQ-007 / DESIGN-REQ-008 Canonical Mode Configuration

Decision: missing.
Evidence: `moonmind/config/settings.py` exposes only `workflow_docker_enabled: bool`; `tests/unit/config/test_settings.py` covers default `True` and `false` override only; no `MOONMIND_WORKFLOW_DOCKER_MODE` parsing or invalid-value rejection exists.
Rationale: The canonical mode surface from `docs/ManagedAgents/DockerOutOfDocker.md` is absent from runtime settings, so default-to-`profiles` and fail-fast invalid values are both unimplemented.
Alternatives considered: Add a compatibility layer that preserves the legacy boolean flag. Rejected because the repo compatibility policy requires replacing superseded internal contracts rather than adding aliases.
Test implications: Add focused unit coverage for default `profiles`, allowed mode parsing, and invalid-mode startup failure.

## FR-004 / DESIGN-REQ-009 Disabled Mode Discovery And Runtime Enforcement

Decision: partial.
Evidence: `build_workload_tool_handler(... workflow_docker_enabled=False)` raises `SkillFailure` in `moonmind/workloads/tool_bridge.py`; `TemporalAgentRuntimeActivities.workload_run` and `security_pentest_execute` raise `docker_workflows_disabled` in `moonmind/workflows/temporal/activity_runtime.py`; tests exist in `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/unit/workflows/temporal/test_activity_runtime.py`.
Rationale: Runtime denial for disabled mode already exists, but `register_workload_tool_handlers()` still registers Docker-backed tools even when disabled, so registry exposure does not yet match the story.
Alternatives considered: Keep tool discovery unchanged and rely only on runtime denial. Rejected because MM-499 requires disabled mode to omit Docker-backed tools from the registry snapshot as well as deny direct invocation.
Test implications: Update unit tests for registration/discovery omission and preserve direct-denial tests; add an integration_ci boundary test that verifies the disabled-mode dispatcher surface is absent or denied consistently.

## FR-005 / DESIGN-REQ-010 Profiles Mode Exposure

Decision: missing.
Evidence: `moonmind/workloads/tool_bridge.py` defines only curated/profile-backed tools (`container.run_workload`, `container.start_helper`, `container.stop_helper`, `moonmind.integration_ci`, `unreal.run_tests`), but no mode-aware selection differentiates profiles mode from unrestricted mode; `worker_runtime.py` always registers the same set whenever the boolean gate is enabled.
Rationale: The repo already has curated/profile-backed tools, but it lacks the policy layer that makes them the default exposed set in `profiles` mode while keeping unrestricted tools unavailable.
Alternatives considered: Treat the current enabled state as equivalent to profiles mode. Rejected because there is no separate unrestricted surface to exclude, so the policy distinction is not actually encoded or testable.
Test implications: Add unit tests for mode-aware registration matrix and integration coverage that the curated/profile-backed tool set is exposed in `profiles` mode.

## FR-006 / DESIGN-REQ-011 Unrestricted Mode Exposure Without Session-Authority Drift

Decision: missing.
Evidence: `docs/ManagedAgents/DockerOutOfDocker.md` defines unrestricted behavior conceptually, but `moonmind/workloads/tool_bridge.py` has no `container.run_container` or `container.run_docker` registration, and `worker_runtime.py`/`activity_runtime.py` have no unrestricted-mode selection logic.
Rationale: The unrestricted-mode contract is not yet represented in runtime code, so neither exposure nor the “session-side Docker authority remains unchanged” invariant can be verified.
Alternatives considered: Defer unrestricted mode entirely and implement only disabled/profiles. Rejected because MM-499 explicitly includes unrestricted mode in scope.
Test implications: Add unit tests for unrestricted-mode tool registration and denial matrix plus integration coverage that unrestricted tools are only reachable in unrestricted mode while session-side behavior remains unchanged.

## FR-007 Discovery/Execution Alignment

Decision: partial.
Evidence: Runtime denial is implemented in both tool handlers and Temporal activity helpers, but registration is unconditional once the boolean gate is true; `tests/integration/temporal/test_integration_ci_tool_contract.py` shows current dispatcher execution for a curated tool without mode differentiation.
Rationale: Discovery and execution share some plumbing, but they do not yet share one mode-aware policy decision, which creates a gap between visible tools and allowed behavior.
Alternatives considered: Add more denial-only tests without changing registration. Rejected because alignment is a product requirement, not just a test concern.
Test implications: Add unit tests around registration + handler wiring, and an integration boundary that validates mode-specific registry/dispatch behavior end to end.

## FR-008 Traceability

Decision: partial.
Evidence: `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-499-moonspec-orchestration-input.md` preserve MM-499 and the original Jira brief.
Rationale: Planning artifacts now need to preserve MM-499, and downstream tasks/verification still need the same traceability.
Alternatives considered: Treat spec-only preservation as sufficient. Rejected because the story explicitly requires downstream traceability for final verification.
Test implications: Final traceability review only.

## Design Artifact Decision

Decision: create a feature-local contract artifact and skip `data-model.md`.
Evidence: MM-499 changes configuration normalization, registry exposure, and runtime policy boundaries, but it does not introduce new persisted entities or stateful domain records.
Rationale: A contract document is needed because the story changes the external configuration surface and the behavior matrix for workload tool exposure. A data model artifact would add noise because the story has no new durable entities.
Alternatives considered: Create `data-model.md` to describe the mode enum. Rejected because the enum belongs in the configuration/contract boundary rather than a persistent data model.
Test implications: Contract review plus traceable unit/integration coverage are sufficient.

## Repo Gap Analysis Outcome

Decision: MM-499 requires production code changes plus tests.
Evidence: The current runtime already has curated Docker workload tooling and disabled-mode denial coverage, but it does not expose the tri-mode configuration contract or mode-aware registration matrix required by the source design.
Rationale: This is not a verification-only story. The repository must replace the boolean `workflow_docker_enabled` contract with one shared mode model and update settings, worker/runtime wiring, and tests accordingly.
Alternatives considered: Treat the story as verification-first because DooD tooling already exists. Rejected because the missing mode contract is central to the requested behavior.
Test implications: Start with focused unit tests for settings and policy wiring, then add/update hermetic integration coverage for mode-aware dispatcher behavior before final full-unit verification.
