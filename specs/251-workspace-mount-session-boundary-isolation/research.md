# Research: Workspace, Mount, and Session-Boundary Isolation

## Story Classification

Decision: Treat MM-502 as a single-story runtime verification-first feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-502-moonspec-orchestration-input.md`; `specs/251-workspace-mount-session-boundary-isolation/spec.md`.
Rationale: The Jira preset brief defines one independently testable runtime outcome: Docker-backed workload launches stay inside MoonMind-owned task paths and remain isolated from managed-session identity and provider auth state.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one bounded story.
Test implications: Unit tests plus at least one hermetic integration boundary are required because the story touches dispatcher/runtime isolation behavior.

## FR-001 / DESIGN-REQ-005 / DESIGN-REQ-014 Workspace Root Enforcement

Decision: implemented_verified.
Evidence: `moonmind/workloads/registry.py` rejects `repoDir`, `artifactsDir`, and `scratchDir` outside `workspace_root`; `moonmind/schemas/workload_models.py` constrains `declaredOutputs` to relative paths under `artifactsDir`; `tests/unit/workloads/test_workload_contract.py` covers profile-backed and unrestricted requests outside the workspace root plus invalid declared outputs.
Rationale: The current repo already enforces the task-workspace boundary at request parsing and registry validation time.
Alternatives considered: Add an extra runtime-only path filter. Rejected because current validation already fails closed before launch.
Test implications: Preserve the existing unit coverage and include final verification traceability.

## FR-002 / DESIGN-REQ-002 / DESIGN-REQ-013 Managed Session Authority Separation

Decision: implemented_unverified.
Evidence: `moonmind/workloads/tool_bridge.py` routes Docker-backed tools through the workload path rather than session verbs; `moonmind/workloads/docker_launcher.py` records session association metadata in `sessionContext`; `moonmind/workflows/temporal/activity_runtime.py` enforces workflow Docker mode at the activity boundary; `tests/unit/workloads/test_workload_tool_bridge.py` and `tests/unit/workflows/temporal/test_workload_run_activity.py` verify session metadata wiring and mode denial.
Rationale: The architecture and unit tests strongly suggest the session/workload authority boundary is already respected, but MM-502 still needs integration proof that a session-assisted workload request does not become session-side Docker authority.
Alternatives considered: Mark implemented_verified from design intent alone. Rejected because the story specifically cares about the boundary at execution time.
Test implications: Add or extend a hermetic integration boundary test that exercises session-assisted workload invocation and confirms the result carries association metadata only.

## FR-003 / DESIGN-REQ-004 / DESIGN-REQ-015 Association Metadata Only

Decision: implemented_unverified.
Evidence: `moonmind/schemas/workload_models.py` only allows `sessionEpoch` and `sourceTurnId` when `sessionId` is present; `moonmind/workloads/docker_launcher.py` exposes session linkage through `sessionContext`; `tests/unit/workloads/test_workload_tool_bridge.py` asserts `sessionContext` is recorded and that `session.summary` is not emitted as an output artifact.
Rationale: The current code clearly models session linkage as association metadata, but there is not yet explicit integration-level proof for the session-assisted path captured by MM-502.
Alternatives considered: Treat the unit proof as final. Rejected because the story is about boundary behavior, not only request-model semantics.
Test implications: Add integration verification that a session-assisted workload run remains a workload result with bounded metadata, not a managed-session continuity artifact.

## FR-004 / DESIGN-REQ-014 / DESIGN-REQ-022 Mount And Output Confinement

Decision: implemented_verified.
Evidence: `moonmind/schemas/workload_models.py` rejects `declaredOutputs` that escape `artifactsDir`; `moonmind/workloads/registry.py` validates workspace-rooted request paths; `moonmind/workloads/docker_launcher.py` calls `_ensure_paths_are_mounted()` before launch; `tests/unit/workloads/test_workload_contract.py` covers invalid declared outputs and workspace escapes.
Rationale: Mount and output confinement already exists in both request normalization and launch preparation.
Alternatives considered: Add redundant mount validation in additional layers. Rejected because current checks already cover both request acceptance and launcher preparation.
Test implications: Existing unit coverage is sufficient unless new integration verification reveals a mismatch.

## FR-005 / DESIGN-REQ-016 Default Credential Isolation

Decision: implemented_verified.
Evidence: `moonmind/schemas/workload_models.py` rejects auth-like profile mounts unless they are declared in explicit `credentialMounts` with `justification` and `approvalRef`; `moonmind/workloads/docker_launcher.py` mounts `profile.credential_mounts` only when explicitly declared; `tests/unit/workloads/test_workload_contract.py` covers rejection of implicit auth-like mounts and acceptance requirements for explicit credential mounts.
Rationale: The current profile contract already fails closed on implicit auth inheritance and requires an explicit, justified credential-mount path.
Alternatives considered: Add a second runtime-only denylist. Rejected because the schema-level contract already encodes the intended rule clearly.
Test implications: Preserve unit coverage and verify traceability in final MoonSpec verification.

## FR-006 / DESIGN-REQ-002 / DESIGN-REQ-004 / DESIGN-REQ-013 / DESIGN-REQ-022 Tool Routing And Runtime Enforcement Alignment

Decision: implemented_unverified.
Evidence: `moonmind/workloads/tool_bridge.py` defines the allowed Docker-backed tool surface and mode-aware gating; `moonmind/workflows/temporal/activity_runtime.py` enforces runtime denial for forbidden modes; `tests/unit/workflows/temporal/test_workload_run_activity.py` verifies deterministic denial for disabled and profiles-only restrictions.
Rationale: Tool registration and runtime enforcement appear aligned, but MM-502 needs end-to-end proof that session-assisted execution follows the same isolation rules as direct workload invocations.
Alternatives considered: Declare no further verification work because the unit activity tests pass. Rejected because the story explicitly covers the real orchestration boundary.
Test implications: Add a hermetic integration test that exercises the dispatcher/runtime boundary with session-associated workload metadata and asserts policy alignment.

## FR-007 Traceability

Decision: implemented_verified.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-502-moonspec-orchestration-input.md`; `specs/251-workspace-mount-session-boundary-isolation/spec.md`; `plan.md`; `research.md`; `contracts/workload-isolation-contract.md`; `quickstart.md`.
Rationale: The feature-local MoonSpec artifact set now preserves MM-502 and the original Jira preset brief for downstream work and verification.
Alternatives considered: Preserve the Jira key only in the source brief. Rejected because the story explicitly requires downstream traceability.
Test implications: Final traceability review only.

## Design Artifact Decision

Decision: create a feature-local contract artifact and skip `data-model.md`.
Evidence: MM-502 changes runtime execution boundaries and verification evidence only; it does not add persisted entities or schema migrations.
Rationale: A contract artifact is necessary because the story is about the allowed/forbidden workload isolation behavior at the tool/runtime boundary. A data model would add noise.
Alternatives considered: Create `data-model.md` for workload request payloads. Rejected because those models already exist in code and this story does not change their persisted shape.
Test implications: Contract review plus unit/integration verification are sufficient.

## Repo Gap Analysis Outcome

Decision: MM-502 likely requires no production-code changes, but it still needs explicit boundary verification for session-assisted workload isolation.
Evidence: Core behavior already exists in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, and `moonmind/workflows/temporal/activity_runtime.py`; the remaining gap is confidence at the dispatcher/runtime boundary for session-associated workload launches.
Rationale: This is primarily a verification and traceability story. Existing code already expresses the required behavior; the orchestration work is to document it in MoonSpec artifacts and prove the boundary with targeted tests.
Alternatives considered: Treat MM-502 as a broad production-code feature. Rejected because the requested behavior appears to already exist and the unresolved question is evidence, not architecture.
Test implications: Run focused workload unit suites plus a hermetic integration boundary that exercises session-associated workload isolation, then use those results in final verification.
