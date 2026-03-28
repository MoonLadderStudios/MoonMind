# Feature Specification: Live Logs Phase 0

**Feature Branch**: `108-live-logs-phase-0`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 0 from docs/tmp/009-LiveLogsPlan.md"

## Source Document Requirements

- **DOC-REQ-001**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Confirm the canonical implementation target is docs/ManagedAgents/LiveLogs.md.")  
  - Summary: The documentation `docs/ManagedAgents/LiveLogs.md` is acknowledged as the primary architectural target.
  
- **DOC-REQ-002**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Inventory current managed-run logging, transcript, tmate, web_ro, and terminal-embed code paths.")  
  - Summary: Identify all active code paths relying on legacy execution and observability relays (tmate, web-ro, transcripts).
  
- **DOC-REQ-003**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Identify all UI surfaces that currently present "Live Output", embedded terminals, or session-viewer semantics.")  
  - Summary: Inventory frontend surfaces tied to terminal-like run output embedding.
  
- **DOC-REQ-004**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Identify current data models and DTOs that store log/session metadata for managed runs.")  
  - Summary: Locate all data models responsible for mapping sessions and run logs.
  
- **DOC-REQ-005**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Identify current artifact-writing paths for stdout, stderr, transcripts, and diagnostics.")  
  - Summary: Identify areas that spool process output asynchronously for retention.

- **DOC-REQ-006**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Decide where the new observability service layer will live in the backend.")  
  - Summary: Make a concrete placement decision for new MoonMind-owned backend log distribution logic.

- **DOC-REQ-007**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Define feature flags for incremental rollout, including a logStreamingEnabled flag.")  
  - Summary: Ensure incremental progression is modeled via feature flags (`logStreamingEnabled`).

- **DOC-REQ-008**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Define the migration boundary between legacy session-based observability and the new MoonMind-owned log model.")  
  - Summary: Plan exactly where and how older runs downgrade dynamically.

- **DOC-REQ-009**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Update any stale docs/specs that still describe tmate web_ro as the primary live-log path.")  
  - Summary: Scrub codebase specification docs of legacy assumptions.

- **DOC-REQ-010**:  
  - Source: `docs/tmp/009-LiveLogsPlan.md` (Phase 0 -> "Create implementation issues/tasks for each phase in this plan.")  
  - Summary: Ensure execution scope produces tasks representing downstream phases.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Codebase Assessment and Auditing (Priority: P1)

An engineer needs a clear mapping of all current code paths touching legacy observability constructs so they do not accidentally break existing workflows when stripping out terminal logic.

**Why this priority**: Without identifying the dependencies or the path of data models across the stack, downstream phases will cause unpredicted regressions.

**Independent Test**: Can be validated by successfully searching and documenting every instance of tmate usage, database DTO mappings to session interfaces, and finding all relevant API routing targets. 

**Acceptance Scenarios**:

1. **Given** the current state of the backend API router, **When** assessing session viewer targets, **Then** all code paths linking task outputs to session viewers will be clearly aggregated.

---

### User Story 2 - Architectural Definition and Strategy (Priority: P1)

A primary MoonMind architect must define integration mechanisms, rollout safety flags (`logStreamingEnabled`), and specific observability microservice routing.

**Why this priority**: Proper rollout depends entirely on concrete runtime gates (feature flags) and structured service layers mapped early before refactoring.

**Independent Test**: Can be tested by reviewing the newly produced service models defining boundary logic, logging logic routes, and feature flag schema additions.

**Acceptance Scenarios**:

1. **Given** an impending rollout, **When** new Phase 1 code connects, **Then** the process operates behind a safely toggleable `logStreamingEnabled` feature flag schema structure.

---

### User Story 3 - Documentation Parity and Blueprint Tracking (Priority: P2)

An engineer maintaining the system relies on `docs/ManagedAgents/LiveLogs.md` remaining accurate and completely divorced from `tmate`-first assumptions across all peripheral documentation.

**Why this priority**: Documentation drift leads to future AI agents repeating past architectural errors.

**Independent Test**: A user can fetch legacy documentations and verify standard operations references lack `web_ro` or explicitly list it as non-managed run.

**Acceptance Scenarios**:

1. **Given** existing system references, **When** the documentation update script runs, **Then** references to `tmate` only define it within its OAuth boundaries, completely disassociated from managed runtime stream ingestion.
2. **Given** Phase 0 completes, **When** downstream tracking iterates, **Then** clearly defined issues/tasks will be established to handle the rest of the plan autonomously.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (Maps to DOC-REQ-002, 003, 004, 005)**: The system MUST identify and catalog all existing usages of legacy observability components across backend, DB interfaces, and UI code via structured research inventory.
- **FR-002 (Maps to DOC-REQ-006)**: The system MUST specify the concrete location boundary for the new observability service endpoints. 
- **FR-003 (Maps to DOC-REQ-007)**: The system MUST define incremental feature rollout flags inside existing schema standards (e.g. `logStreamingEnabled`).
- **FR-004 (Maps to DOC-REQ-008)**: The system MUST establish explicitly how old workflows gracefully degrade outside the streaming umbrella.
- **FR-005 (Maps to DOC-REQ-001)**: The system MUST assert `docs/ManagedAgents/LiveLogs.md` is the canonical reference tracking implementation parity.
- **FR-006 (Maps to DOC-REQ-009)**: The system MUST scrub and correct out-of-date internal readmes/specs to remove `tmate web_ro` from managed logs context.
- **FR-007 (Maps to DOC-REQ-010)**: The system MUST emit concrete executable implementation tasks orchestrating the subsequent phases.
- **FR-008**: The execution MUST result in production runtime deliverables handling updates mapping boundary configurations alongside validation/verification assertions covering these mappings.

### Key Entities

- **Legacy Session Definitions**: References in config or DTO mapping (`TaskRunLiveSession`).
- **Log Streaming Configurations**: Environment variables or flag interfaces for safely disabling stream behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of explicit `tmate`/`web_ro` backend/frontend intersections related strictly to observability will be mapped to a migration checklist task.
- **SC-002**: 100% of `DOC-REQ-*` source requirement identifiers will have an established path inside execution scope files.
- **SC-003**: The feature rollout configuration boundary (`logStreamingEnabled`) is implemented structurally in configuration management.
- **SC-004**: No ambiguous or conflicting architectural documentation definitions persist around basic execution observation flow. 
