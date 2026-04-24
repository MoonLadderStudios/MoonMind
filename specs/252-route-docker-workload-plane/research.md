# Research: Shared Docker Workload Execution Plane

## Story Classification

Decision: Treat MM-503 as a single-story runtime verification-first feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md`; `specs/252-route-docker-workload-plane/spec.md`.
Rationale: The Jira preset brief defines one independently testable runtime outcome: all Docker-backed MoonMind tools execute through one trusted workload plane with shared routing, metadata, timeout, cancellation, and cleanup behavior.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one bounded story rather than multiple independently testable stories.
Test implications: Unit tests plus hermetic integration verification are required because the story spans tool definitions, activity routing, launcher metadata, and cleanup behavior.

## FR-001 / DESIGN-REQ-006 / DESIGN-REQ-020 Shared Logical Capability Routing

Decision: implemented_unverified.
Evidence: `moonmind/workloads/tool_bridge.py` registers curated and unrestricted DooD tools with `mm.tool.execute` and `docker_workload` capability; `moonmind/workflows/temporal/activity_runtime.py` seeds the default skill registry with the same capability requirements; `tests/unit/workloads/test_workload_tool_bridge.py` and `tests/unit/workflows/temporal/test_activity_runtime.py` verify tool definitions and registry payloads for `container.run_workload`, helper tools, unrestricted tools, and `unreal.run_tests`.
Rationale: The current code strongly expresses the intended logical capability contract, but MM-503 still needs explicit verification that the observable contract is defined by the logical `docker_workload` capability rather than by today’s fleet placement assumptions.
Alternatives considered: Treat current unit evidence as fully verified. Rejected because the story explicitly cares about preserving the contract if physical fleet placement changes.
Test implications: Add or extend focused unit coverage around activity bindings and capability requirements, plus one integration assertion that no alternate execution path is used for supported DooD tools.

## FR-002 / DESIGN-REQ-019 Deterministic Metadata And Labels

Decision: partial.
Evidence: `moonmind/workloads/registry.py` derives ownership labels for task run, step, attempt, tool name, session linkage, and workload access class; `moonmind/workloads/docker_launcher.py` augments operational labels and publishes workload metadata including `labels`, `timeoutReason`, and `sessionContext`; `tests/unit/workloads/test_workload_contract.py` and `tests/unit/workloads/test_docker_workload_launcher.py` verify deterministic label derivation and launcher metadata publication.
Rationale: Most required metadata is present today, especially task/run identity and workload access class, but MM-503 specifically calls out runtime mode and cross-class consistency, and that coverage is not yet proven across the unrestricted paths.
Alternatives considered: Mark implemented_unverified instead of partial. Rejected because the current evidence suggests a plausible metadata gap around explicit runtime mode labeling or equivalent cross-class metadata.
Test implications: Verify metadata shape for profile-backed, helper, unrestricted container, and Docker CLI paths first; add implementation only if required metadata is absent or inconsistent.

## FR-003 Shared Outcome Contract Across Launch Classes

Decision: implemented_unverified.
Evidence: `moonmind/workloads/tool_bridge.py` normalizes outputs into `workloadResult`, `workloadMetadata`, and artifact publication metadata; `moonmind/workloads/docker_launcher.py` publishes bounded metadata and artifact publication details; `tests/unit/workloads/test_workload_tool_bridge.py` and `tests/integration/temporal/test_profile_backed_workload_contract.py` verify this for profile-backed and helper flows.
Rationale: The result-shaping path exists and is covered for curated/structured flows, but unrestricted container and Docker CLI paths still need explicit proof that they share the same observable workload result contract.
Alternatives considered: Declare the shared contract complete from the common tool bridge alone. Rejected because the story requires cross-class evidence, not only shared code inspection.
Test implications: Add unit and integration verification for unrestricted tool outputs before deciding whether any code changes are necessary.

## FR-004 / DESIGN-REQ-023 Timeout And Cancellation Consistency

Decision: implemented_unverified.
Evidence: `moonmind/workloads/docker_launcher.py` owns request timeout, kill grace, bounded shutdown, diagnostics capture, and timeout metadata; `tests/unit/workloads/test_docker_workload_launcher.py` exercises launcher timeout and artifact publication behavior; `tests/integration/temporal/test_profile_backed_workload_contract.py` verifies helper teardown reasons at the dispatcher/runtime boundary.
Rationale: Structured workload paths already show strong timeout and cancellation handling, but MM-503 needs cross-class proof that the same lifecycle semantics hold for unrestricted container and Docker CLI executions.
Alternatives considered: Add blanket production hardening immediately. Rejected because the existing launcher may already satisfy the requirement and first needs verification across tool classes.
Test implications: Extend launcher- and dispatcher-level verification to unrestricted paths; implement only if lifecycle metadata or shutdown behavior diverges.

## FR-005 / DESIGN-REQ-023 / DESIGN-REQ-024 Cleanup Ownership Boundaries

Decision: implemented_unverified.
Evidence: `moonmind/workloads/docker_launcher.py` distinguishes owned structured cleanup from label-based best-effort janitor behavior; `tests/unit/workloads/test_docker_workload_launcher.py` covers janitor label lookup, helper cleanup, and artifact publication; `tests/unit/workloads/test_workload_contract.py` verifies ownership labels for structured and unrestricted requests.
Rationale: The intended cleanup split is visible in code, but MM-503 still needs explicit proof that arbitrary Docker-created resources are not over-cleaned while structured workload resources remain owned and cleaned deterministically.
Alternatives considered: Treat code comments and janitor unit tests as sufficient. Rejected because the story is specifically about the shared execution-plane contract at runtime.
Test implications: Add focused cleanup-boundary verification; implementation contingency only if the launcher or janitor currently overreaches.

## FR-006 Physical Fleet Independence

Decision: implemented_unverified.
Evidence: `docs/ManagedAgents/DockerOutOfDocker.md` states the logical `docker_workload` capability is stable while physical placement may change; `moonmind/workloads/tool_bridge.py` and `tests/unit/workflows/temporal/test_activity_runtime.py` assert capability-based execution rather than direct fleet-specific tool routing.
Rationale: The contract is designed correctly, but there is not yet explicit repo evidence showing that runtime-facing behavior would remain unchanged if the capability moved off the current fleet.
Alternatives considered: Mark implemented_verified because the current binding already uses capability selection. Rejected because the story wants that guarantee preserved and testable.
Test implications: Focus on unit verification of capability-based bindings and final traceability review rather than integration environment changes.

## FR-007 Traceability

Decision: implemented_verified.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md`; `specs/252-route-docker-workload-plane/spec.md`; `plan.md`; `research.md`; `contracts/shared-docker-workload-plane-contract.md`; `quickstart.md`.
Rationale: The feature-local MoonSpec artifact set preserves MM-503 and the original Jira preset brief for downstream planning and final verification.
Alternatives considered: Preserve the Jira key only in the orchestration brief. Rejected because the story explicitly requires downstream spec and verification traceability.
Test implications: Final traceability review only.

## Design Artifact Decision

Decision: create a feature-local contract artifact and skip `data-model.md`.
Evidence: MM-503 concerns routing, metadata, timeout/cancellation behavior, cleanup ownership, and capability binding, not new persisted entities or migrations.
Rationale: A contract artifact is necessary because the story is about the public runtime behavior of the shared workload execution plane across multiple tool classes. A data model would add noise because the existing request/result models already exist in code and are not the primary planned change surface.
Alternatives considered: Create `data-model.md` for workload result metadata. Rejected because the story does not introduce a new persisted or externally versioned data model.
Test implications: Contract review plus unit/integration verification are sufficient.

## Repo Gap Analysis Outcome

Decision: MM-503 likely requires test-first verification plus small boundary hardening rather than a large production refactor.
Evidence: Core routing and workload lifecycle behavior already exist in `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/registry.py`, and `moonmind/workflows/temporal/activity_runtime.py`; current tests already cover profile-backed routing, helper lifecycle, unrestricted tool registration, launcher metadata, and cleanup primitives.
Rationale: The unresolved question is whether the current implementation fully satisfies the cross-class shared-plane contract demanded by MM-503, especially around metadata completeness and cleanup semantics for unrestricted paths.
Alternatives considered: Treat MM-503 as a broad architecture build-out. Rejected because the shared execution plane already exists and the remaining risk is verification and targeted hardening.
Test implications: Run focused workload unit suites plus hermetic integration verification for the dispatcher/runtime boundary; add implementation only where verification exposes a real cross-class gap.
