# Feature Specification: Typed Workflow Messages

**Feature Branch**: `174-typed-workflow-messages`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**: User description: "MM-329: Type workflow inputs, messages, continuation state, and managed-session controls

User Story
As an operator relying on long-running managed sessions, I need workflow run inputs, Continue-As-New state, Signals, Updates, Queries, and managed-session controls to use explicit typed contracts with validators, epoch safety, idempotency, and serialized mutation handling so live workflows remain deterministic and controllable.
Source Document
docs/Temporal/TemporalTypeSafety.md
Source Sections
- 6 Workflow run / Continue-As-New inputs
- 7 Signals, Updates, and Queries
- 7.3 Validators are mandatory for mutating Updates
- 8 Managed-session-specific rules
- 8.2-8.5 Managed-session-specific rules
Coverage IDs
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-016
Story Metadata
- Story ID: STORY-003
- Short name: typed-workflow-messages
- Breakdown JSON: docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the request is "Implement Docs/<path>.md", treat it as runtime intent and use the document as source requirements.
Source design path (optional): ."

## User Story - Typed Managed Session Controls

**Summary**: As an operator relying on long-running managed sessions, I want managed-session workflow inputs, continuation state, and control messages to use explicit typed contracts so live sessions remain deterministic, epoch-safe, idempotent, and controllable.

**Goal**: Operators and maintainers can evolve and operate managed-session workflows without relying on loose message bags for session controls or continuation state.

**Independent Test**: Can be fully tested by validating managed-session contract models, exercising workflow control validators, and running a workflow lifecycle scenario that sends signals, updates, queries status, and carries state through Continue-As-New.

**Acceptance Scenarios**:

1. **Given** a managed-session workflow receives runtime handles, **When** the handle signal is accepted, **Then** the message is validated through a named typed contract and rejects unknown or blank fields before mutating workflow state.
2. **Given** a mutating managed-session update targets an active session, **When** stale epoch, duplicate completed request, illegal state, or invalid shape is submitted, **Then** the validator rejects the update before workflow history accepts the mutation.
3. **Given** a managed session crosses a Continue-As-New threshold, **When** the workflow builds continuation state, **Then** the payload validates as the named workflow input model and carries only bounded session identity, runtime handles, continuity refs, and request tracking state.
4. **Given** multiple mutating controls target the same session, **When** updates execute concurrently, **Then** conflicting mutations are serialized and request tracking remains bounded and idempotent.
5. **Given** an operator queries session state, **When** the workflow returns status, **Then** the projection comes from a typed session snapshot and contains bounded operator-visible fields.

### Edge Cases

- Stale `sessionEpoch` values are rejected for epoch-sensitive controls before mutation work starts.
- Completed request identifiers cannot be reused for the same operation, and a request identifier cannot be reused across different control actions.
- Continue-As-New request tracking is capped so long-running sessions do not grow unbounded workflow payloads.
- Legacy catch-all `control_action` signal remains a replay compatibility shim and is not the canonical client-facing control surface.

## Assumptions

- The current story scope is the Codex task-scoped managed-session workflow, because it is the active managed-session plane and the source document names managed sessions as the highest-value Temporal message surface.
- The absent breakdown JSON path is treated as unavailable in this checkout; the task instruction and source document sections are the canonical source.

## Source Design Requirements

- **DESIGN-REQ-012**: `docs/Temporal/TemporalTypeSafety.md` section 6 requires workflow run and Continue-As-New payloads to use named typed input contracts, with continuation state modeled intentionally and minimally. Scope: in scope. Maps: FR-001, FR-002, FR-003.
- **DESIGN-REQ-013**: `docs/Temporal/TemporalTypeSafety.md` section 7 requires Signals, Updates, and Queries to have named typed request or response models instead of canonical raw dictionaries. Scope: in scope. Maps: FR-004, FR-005, FR-010.
- **DESIGN-REQ-014**: `docs/Temporal/TemporalTypeSafety.md` section 7.3 requires every mutating Update to have a validator that rejects invalid shape, stale epochs, illegal states, or duplicate misuse before history acceptance. Scope: in scope. Maps: FR-006, FR-007.
- **DESIGN-REQ-015**: `docs/Temporal/TemporalTypeSafety.md` sections 8.2-8.4 require managed-session controls to be epoch-aware, idempotent, bounded across Continue-As-New, and serialized when mutations conflict. Scope: in scope. Maps: FR-006, FR-007, FR-008, FR-009.
- **DESIGN-REQ-016**: `docs/Temporal/TemporalTypeSafety.md` section 8.5 requires workflow-owned session status to come from an explicit snapshot model rather than ad hoc query reconstruction. Scope: in scope. Maps: FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Managed-session workflow `run` input MUST use a named typed workflow input contract.
- **FR-002**: Managed-session workflow initialization MUST use the same named workflow input contract as `run` so state exists before messages are processed.
- **FR-003**: Managed-session Continue-As-New payloads MUST validate as the named workflow input contract and carry only intentional bounded continuation fields.
- **FR-004**: The runtime-handle signal MUST use a named typed request contract and reject unknown or invalid fields.
- **FR-005**: Each canonical managed-session mutating Update MUST have an explicit named request model for its operation.
- **FR-006**: Mutating Update validators MUST validate request shape and reject stale epochs for epoch-sensitive operations before mutation work starts.
- **FR-007**: Mutating Update validators MUST reject duplicate completed request misuse and cross-action request identifier reuse.
- **FR-008**: Mutating managed-session controls MUST serialize conflicting workflow mutations.
- **FR-009**: Request tracking preserved across Continue-As-New MUST remain typed and bounded.
- **FR-010**: Managed-session query status MUST be derived from a named typed session snapshot contract.
- **FR-011**: Legacy catch-all managed-session control messages MUST remain marked as compatibility shims and must not become the canonical control surface.

### Key Entities

- **Managed Session Workflow Input**: Bounded identity, runtime, continuation refs, handles, and request tracking state needed to start or continue one managed session.
- **Managed Session Control Request**: One named request model per canonical mutating operation such as follow-up, steer, interrupt, clear, cancel, and terminate.
- **Managed Session Snapshot**: Typed workflow-owned projection of session state for operator-visible queries.
- **Request Tracking Entry**: Bounded idempotency record for mutating managed-session controls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove named managed-session message models reject invalid or unknown fields.
- **SC-002**: Unit tests prove mutating Update validators reject stale epochs, duplicate misuse, and illegal states.
- **SC-003**: Unit tests prove Continue-As-New payload construction returns the named workflow input model with bounded request tracking state.
- **SC-004**: A workflow lifecycle test exercises signal, update, query, and control flows with typed message contracts.
