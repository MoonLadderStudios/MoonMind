# Research: Profile-Backed Workload Contracts

## Story Classification

Decision: Treat MM-500 as a single-story runtime verification-first feature request.
Evidence: `spec.md` (Input); `specs/249-profile-backed-workload-contracts/spec.md`.
Rationale: The Jira preset brief defines one independently testable runtime outcome: profile-backed workload and helper tools stay on approved runner profiles rather than widening into arbitrary raw container inputs.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one bounded story.
Test implications: Unit tests plus at least one hermetic integration boundary are required because the story touches dispatcher/runtime execution behavior.

## FR-001 / DESIGN-REQ-017 Profile-Backed `container.run_workload`

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` defines `container.run_workload` as a profile-backed tool requiring `profileId`; `moonmind/schemas/workload_models.py` validates `WorkloadRequest`; `tests/unit/workloads/test_workload_tool_bridge.py` verifies the tool definition and handler behavior; `tests/integration/temporal/test_profile_backed_workload_contract.py` verifies dispatcher execution against an approved runner profile.
Rationale: The one-shot workload tool already routes through the existing runner-profile model and no production gap remained after adding the integration boundary.
Alternatives considered: Classify as implemented_unverified. Rejected after the dispatcher-boundary integration test confirmed the same contract at the integration layer.
Test implications: Maintain unit plus integration evidence.

## FR-002 / DESIGN-REQ-012 / DESIGN-REQ-018 Runner Profile Resolution And Launch Policy

Decision: implemented_verified.
Evidence: `moonmind/workloads/registry.py` resolves and validates profile-backed requests; `moonmind/workloads/docker_launcher.py` applies runner-profile mounts, env policy, timeout, cleanup, and helper lifecycle; `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py` cover these behaviors.
Rationale: The current repo already enforces runner-profile-defined policy at validation and launch time, and the integration boundary now proves the dispatcher reaches that validated path.
Alternatives considered: Add production-code changes to restate existing policy. Rejected because the behavior already exists and the missing gap was proof, not implementation.
Test implications: Preserve unit and integration coverage for registry validation and launcher behavior.

## FR-003 / DESIGN-REQ-017 Raw Container Input Rejection

Decision: implemented_verified.
Evidence: `moonmind/schemas/workload_models.py` and `moonmind/workloads/tool_bridge.py` reject unsupported request fields; `tests/unit/workloads/test_workload_tool_bridge.py` covers `image`, `mounts`, `devices`, and `privileged`; `tests/integration/temporal/test_profile_backed_workload_contract.py` verifies dispatcher-boundary rejection of raw fields.
Rationale: The profile-backed tool surface already fails closed against raw container-shape input.
Alternatives considered: Add more request-model complexity or a separate rejection layer. Rejected because the existing contract already expresses the policy cleanly.
Test implications: Keep both unit and integration invalid-input coverage.

## FR-004 / DESIGN-REQ-012 / DESIGN-REQ-018 Bounded Helper Lifecycle

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` maps `container.start_helper` and `container.stop_helper`; `moonmind/workloads/docker_launcher.py` implements readiness and teardown behavior; `moonmind/workflows/temporal/activity_runtime.py` dispatches helper tools by `toolName`; `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py` verify helper lifecycle metadata.
Rationale: Helper lifecycle ownership is already explicitly bounded and the integration boundary now proves it through the tool dispatcher.
Alternatives considered: Treat helpers as detached services. Rejected because the source design explicitly forbids that shape.
Test implications: Keep unit and integration evidence for readiness and teardown behavior.

## FR-005 / DESIGN-REQ-025 Curated Tool Alignment

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` keeps `unreal.run_tests` and `moonmind.integration_ci` on curated runner profiles; `tests/unit/workloads/test_workload_tool_bridge.py` verifies curated command/profile behavior; `tests/integration/temporal/test_integration_ci_tool_contract.py` verifies profile-backed integration-ci routing.
Rationale: Curated domain tools already align with the same runner-profile-backed execution model.
Alternatives considered: Add duplicate curated-tool tests inside MM-500. Rejected because existing verified integration evidence already covers that adjacent boundary.
Test implications: Final verification should reuse the existing curated-tool evidence rather than add redundant runtime code.

## FR-006 Disabled-Mode Denial

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` denies forbidden tools via `tool_allowed_for_workflow_docker_mode`; `moonmind/workflows/temporal/activity_runtime.py` returns deterministic application errors for disabled mode; `tests/unit/workflows/temporal/test_workload_run_activity.py` and `tests/integration/temporal/test_profile_backed_workload_contract.py` verify denial.
Rationale: Disabled-mode denial is already implemented and now verified at the dispatcher boundary for the MM-500 tool path.
Alternatives considered: Add separate runtime code for profile-backed denial. Rejected because current behavior already matches the story.
Test implications: Preserve deterministic-denial integration coverage.

## FR-007 Traceability

Decision: implemented_verified.
Evidence: `spec.md` (Input); `specs/249-profile-backed-workload-contracts/spec.md`; `plan.md`; `research.md`; `contracts/profile-backed-workload-contract.md`; `quickstart.md`; `tasks.md`.
Rationale: The feature-local MoonSpec artifact set now preserves MM-500 and the original Jira preset brief for downstream work and verification.
Alternatives considered: Preserve the Jira key only in the source brief. Rejected because the story explicitly requires downstream traceability.
Test implications: Final traceability review only.

## Design Artifact Decision

Decision: create a feature-local contract artifact and skip `data-model.md`.
Evidence: MM-500 changes runtime tool-contract verification and traceability only; it does not add persisted domain entities or new stored state.
Rationale: A contract artifact is necessary because the story is about the profile-backed workload/helper tool surface and its allowed/forbidden inputs. A data model would add noise.
Alternatives considered: Create `data-model.md` for runner profiles. Rejected because runner profiles are already modeled in repo code and this story does not change their persisted shape.
Test implications: Contract review plus unit/integration verification are sufficient.

## Repo Gap Analysis Outcome

Decision: MM-500 required no production-code changes after adding one missing hermetic integration boundary.
Evidence: Core runtime behavior already existed in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, and `moonmind/workflows/temporal/activity_runtime.py`; the only missing evidence was a dispatcher-boundary `integration_ci` test for the existing profile-backed contract.
Rationale: This is primarily a verification and traceability story. The repo already implements the required behavior; the orchestration work was to document it in MoonSpec artifacts, add the missing integration proof, and validate the result.
Alternatives considered: Treat MM-500 as code-implementation work. Rejected because the requested behavior was already present and stable in the codebase.
Test implications: Run the focused workload unit suites plus the targeted hermetic integration boundary and use those results in final verification.
