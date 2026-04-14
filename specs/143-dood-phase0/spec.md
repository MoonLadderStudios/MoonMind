# Feature Specification: Docker-Out-of-Docker Phase 0 Contract Lock

**Feature Branch**: `143-dood-phase0`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 using test-driven development of the DooD plan."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Align the canonical DooD glossary and boundary docs (Priority: P1)

MoonMind engineers need the canonical Docker-out-of-Docker documentation set to state one consistent architectural boundary before Phase 1 code work begins, so later implementation does not split across conflicting terms or lifecycle assumptions.

**Why this priority**: Phase 0 exists specifically to freeze terminology and boundary rules before launcher and tool-path code spreads in multiple directions.

**Independent Test**: Read `docs/ManagedAgents/DockerOutOfDocker.md`, `docs/ManagedAgents/CodexCliManagedSessions.md`, and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` and confirm they use the same glossary for `session container`, `workload container`, `runner profile`, and `session-assisted workload`.

**Acceptance Scenarios**:

1. **Given** the canonical DooD doc defines the workload architecture, **When** engineers review the related session-plane and execution-model docs, **Then** those docs cross-reference the same glossary instead of introducing conflicting container identity terms.
2. **Given** the DooD strategy distinguishes task-scoped managed sessions from specialized workload containers, **When** the three canonical docs are reviewed together, **Then** each document preserves that separation without implying a workload container is session continuity state.

---

### User Story 2 - Freeze the execution primitive and lifecycle scope (Priority: P1)

MoonMind engineers need the execution model and session-plane docs to state that Docker-backed workload launches enter through ordinary executable tools first, remain outside `MoonMind.AgentRun` identity unless they are true agent runtimes, and focus the MVP on one-shot workload containers.

**Why this priority**: The highest-risk ambiguity in the current plan is whether specialized containers should be modeled as agent sessions, helper services, or tool executions.

**Independent Test**: Read the three canonical docs and confirm they all state that Docker-backed workloads are ordinary executable tools, not new managed sessions, and that Phase 1 through Phase 4 target one-shot workload containers first.

**Acceptance Scenarios**:

1. **Given** a managed Codex step requests a specialized toolchain, **When** the execution boundary is documented, **Then** the launch path is described as a control-plane tool invocation using `tool.type = "skill"` rather than a child `MoonMind.AgentRun`.
2. **Given** bounded helper containers are a later direction, **When** the canonical docs describe current scope, **Then** they explicitly keep helper containers out of the MVP and preserve one-shot workload containers as the first implementation target.

---

### User Story 3 - Leave durable implementation tracking and executable validation (Priority: P2)

MoonMind maintainers need a temporary implementation tracker plus an automated test that guards the new Phase 0 wording so the canonical docs and backlog references do not drift immediately after the contract lock lands.

**Why this priority**: Phase 0 is only useful if the frozen boundary remains checkable and the unfinished rollout work stays linked from the canonical docs.

**Independent Test**: Run the focused unit test suite for the DooD Phase 0 documentation contract and confirm it passes while the remaining-work tracker exists and is referenced from the canonical doc set.

**Acceptance Scenarios**:

1. **Given** the canonical DooD doc points to unfinished implementation work, **When** maintainers inspect `docs/tmp/remaining-work/`, **Then** a Phase 0 tracker exists for the DooD rollout and is referenced from the canonical doc.
2. **Given** future edits could reintroduce ambiguous language, **When** the new automated validation runs, **Then** it fails if the required cross-reference or execution-boundary wording disappears from the canonical docs.

### Edge Cases

- A future documentation edit keeps the DooD glossary in one file but removes the session-plane or execution-model cross-reference, creating silent terminology drift.
- A future edit reclassifies Docker-backed workload tools as managed sessions or `MoonMind.AgentRun` instances without an explicit architecture change.
- The DooD implementation tracker is deleted or renamed while the canonical doc still links to it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `docs/ManagedAgents/DockerOutOfDocker.md` MUST remain the canonical desired-state document for MoonMind's Docker-backed specialized workload-container architecture.
- **FR-002**: `docs/ManagedAgents/DockerOutOfDocker.md`, `docs/ManagedAgents/CodexCliManagedSessions.md`, and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` MUST use one consistent glossary for `session container`, `workload container`, `runner profile`, and `session-assisted workload`.
- **FR-003**: `docs/ManagedAgents/CodexCliManagedSessions.md` MUST state that the managed session plane may invoke control-plane tools that launch separate workload containers, but those workload containers remain outside session identity.
- **FR-004**: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` MUST state that Docker-backed workload launches are ordinary executable tools and MUST NOT be treated as new `MoonMind.AgentRun` instances unless the launched runtime is itself a true managed agent runtime.
- **FR-005**: The canonical documentation set MUST state that the initial DooD implementation scope for Phases 1 through 4 is one-shot workload containers and that bounded helper containers remain a later phase.
- **FR-006**: The canonical documentation set MUST state that `tool.type = "skill"` is the initial execution primitive for Docker-backed workload launches and MUST preserve `tool.type = "agent_runtime"` for true long-lived agent runtimes only.
- **FR-007**: `docs/tmp/remaining-work/` MUST contain a DooD implementation tracker linked from the canonical DooD doc and summarizing the remaining phased rollout work after Phase 0.
- **FR-008**: The Phase 0 implementation MUST include automated validation that fails when the required DooD/session-plane/execution-model wording or remaining-work tracker reference is missing.

### Key Entities *(include if feature involves data)*

- **Session Container**: The task-scoped managed Codex container that carries session continuity within one task.
- **Workload Container**: The separate specialized non-agent container launched through the control plane to perform a bounded workload.
- **Runner Profile**: The curated MoonMind-owned workload-container definition that constrains image, mounts, environment, resources, and policy.
- **Session-Assisted Workload**: A managed-session step that requests a specialized workload through the control plane without inheriting unrestricted Docker authority.
- **DooD Remaining-Work Tracker**: The temporary backlog file in `docs/tmp/remaining-work/` that records unfinished rollout phases linked from the canonical DooD doc.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The canonical DooD, session-plane, and execution-model docs can be read together without contradicting each other on container identity, glossary, or execution primitive.
- **SC-002**: The canonical docs explicitly preserve `tool.type = "skill"` for the initial Docker-backed workload path and reserve `tool.type = "agent_runtime"` for true managed runtimes.
- **SC-003**: A DooD implementation tracker exists under `docs/tmp/remaining-work/` and is linked from the canonical DooD doc.
- **SC-004**: Automated unit validation for the Phase 0 documentation contract passes through `./tools/test_unit.sh` using a targeted DooD Phase 0 test file.
