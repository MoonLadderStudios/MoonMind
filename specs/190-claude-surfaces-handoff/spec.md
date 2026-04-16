# Feature Specification: Claude Surfaces Handoff

**Feature Branch**: `190-claude-surfaces-handoff`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-348 from MM project
Summary: MoonSpec STORY-007: Implement multi-surface projection and handoff contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-348 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-348: MoonSpec STORY-007: Implement multi-surface projection and handoff contracts

User Story
As a user moving between terminal, IDE, desktop, web, mobile, scheduler, and SDK contexts, I need surface attachment and handoff semantics that preserve where Claude is actually executing.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.2 Separate execution from presentation
- 7.3 Critical execution distinctions
- 16. Multi-surface behavior
- 17.1 Session APIs
- 18.2 Surface events
- 21.6 Cloud vs local security

Coverage IDs
- DESIGN-REQ-002
- DESIGN-REQ-004
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-024
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-007
- Short name: claude-surfaces-handoff
- Dependency mode: none
- Story dependencies from breakdown: STORY-001

Independent Test
Attach and detach terminal, web, mobile, scheduler, and SDK surfaces to local and cloud sessions, simulate disconnect/reconnect, and perform local-to-cloud handoff while asserting execution_owner, projection_mode, lineage, and events.

Acceptance Criteria
- A session may have one primary surface and multiple projections.
- Remote Control adds a SurfaceBinding without changing execution_owner or minting a new session.
- Cloud handoff creates a new session with execution_owner = anthropic_cloud_vm and handoff lineage to the source session.
- Surface disconnects do not fail sessions unless the runtime exits.
- Resuming on a different surface preserves canonical session identity when execution owner remains the same.
- Security and compliance reporting can distinguish local execution, Remote Control projection, and cloud execution.

Requirements
- Expose durable SurfaceBinding records and lifecycle events.
- Keep execution semantics independent from presentation surface.
- Represent handoff artifacts as lineage seed references.
- Avoid reimplementing Anthropic proprietary local protocol in the plane.

Implementation Notes
- Treat presentation surfaces as attachable views over an execution owner, not as independent sessions by default.
- Model durable `SurfaceBinding` records with at most one primary surface and any number of projections.
- Ensure Remote Control projection adds a binding while preserving the original session identity and execution owner.
- Represent local-to-cloud handoff as a new session whose execution owner is `anthropic_cloud_vm` and whose lineage references the source session through handoff seed artifacts.
- Preserve canonical session identity across disconnect/reconnect and cross-surface resume when the execution owner has not changed.
- Emit normalized lifecycle events for surface attachment, detachment, disconnect, reconnect, resume, and handoff so downstream audit and workflow-boundary tests can assert behavior.
- Keep security and compliance reporting able to distinguish local execution, Remote Control projection, and cloud execution.
- Do not reimplement Anthropic proprietary local transport; keep MoonMind at the orchestration, identity, lineage, event, and contract boundary.

Out of Scope
- Actual Claude proprietary transport reimplementation.
- Checkpoint restore internals.
- Enterprise telemetry dashboards.

Source Design Coverage
- DESIGN-REQ-002: Covered by preserving execution semantics separately from terminal, IDE, desktop, web, mobile, scheduler, and SDK presentation surfaces.
- DESIGN-REQ-004: Covered by modeling Remote Control as projection without changing execution owner or creating a new canonical session.
- DESIGN-REQ-019: Covered by maintaining session identity, usage, resume, archive, lineage, communication, and surface semantics across attachment and handoff.
- DESIGN-REQ-020: Covered by requiring explicit session and surface API behavior for attach, detach, resume, and handoff paths.
- DESIGN-REQ-024: Covered by security reporting that distinguishes local execution, Remote Control projection, and cloud execution.
- DESIGN-REQ-028: Covered by normalized lifecycle events for surface binding, projection, disconnect/reconnect, resume, and cloud handoff.

Needs Clarification
- [NEEDS CLARIFICATION] How should cloud handoff summaries be versioned and audited?
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Surface Projection And Handoff

**Summary**: As a user moving between Claude surfaces, I want surface attachment and handoff semantics to preserve where Claude is actually executing so that local execution, Remote Control projection, and cloud execution remain auditable and distinct.

**Goal**: Operators and downstream workflows can inspect a Claude managed session and determine which surfaces are attached, whether a surface is primary or a projection, whether disconnects are presentation-only, and whether a cloud handoff created a new cloud-owned session with lineage to the source.

**Independent Test**: Attach local and Remote Control surfaces to a local Claude session, disconnect and reconnect a projection, resume on another local surface, perform a cloud handoff, and assert session identity, execution owner, projection mode, handoff lineage, seed artifact references, and normalized surface events.

**Acceptance Scenarios**:

1. **Given** a Claude session has a primary local surface, **when** additional web or mobile Remote Control surfaces attach, **then** the system records durable projection bindings without changing the session identity or execution owner.
2. **Given** a projected surface disconnects, **when** the runtime itself remains active, **then** the session does not fail and the binding records a disconnected or reconnecting lifecycle state.
3. **Given** a user resumes the same locally owned session from a different surface, **when** execution ownership remains unchanged, **then** canonical session identity is preserved and the primary surface is updated through surface binding state.
4. **Given** a user performs a local-to-cloud handoff, **when** a destination session is created, **then** the destination uses cloud execution ownership, references the source session, carries bounded seed artifact references, and does not reuse Remote Control projection fields.
5. **Given** surface lifecycle activity occurs, **when** downstream audit consumers inspect events, **then** attach, connect, disconnect, reconnect, detach, resume, and handoff events use normalized names and bounded metadata.
6. **Given** security or compliance reporting inspects Claude execution state, **when** it compares local execution, Remote Control projection, and cloud execution, **then** each mode is distinguishable without inferring execution from UI surface alone.

### Edge Cases

- A session cannot have more than one primary surface binding at the same time.
- A Remote Control projection must not mutate `execution_owner`.
- A cloud handoff destination cannot reuse the source `session_id`.
- Handoff seed artifact references must be bounded, nonblank lineage pointers rather than embedded summaries or checkpoint payloads.
- Unsupported surface lifecycle event names or unsupported projection modes fail validation instead of being coerced.

## Assumptions

- MM-348 builds on the MM-342 Claude session core contracts and does not replace the existing `ClaudeManagedSession` schema.
- The unresolved handoff-summary question is handled by storing versionable seed artifact references for this story; full summary payload versioning and audit policy remain out of scope.
- Runtime validation can use deterministic fixtures and schema-boundary tests before live Claude provider surface integration is available.

## Source Design Requirements

- **DESIGN-REQ-002**: Source sections 2.2 and 7 require execution owner, surface binding, and projection mode to remain separate so UI surface never implies execution location. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-014.
- **DESIGN-REQ-004**: Source section 7.3 requires Remote Control projection to remain distinct from cloud handoff. Scope: in scope. Maps to FR-005, FR-006, FR-007, FR-008, and FR-009.
- **DESIGN-REQ-019**: Source sections 16 and 17.1 require attach, detach, resume, lineage, and surface semantics across session APIs. Scope: in scope. Maps to FR-010, FR-011, FR-012, and FR-013.
- **DESIGN-REQ-020**: Source section 18.2 requires normalized surface lifecycle events. Scope: in scope. Maps to FR-015, FR-016, and FR-017.
- **DESIGN-REQ-024**: Source section 21.6 requires local execution, Remote Control projection, and cloud execution to remain distinguishable for security and compliance reporting. Scope: in scope. Maps to FR-014 and FR-018.
- **DESIGN-REQ-028**: Source section 16.3 and the MM-348 brief require handoff artifacts to be represented as lineage seed references. Scope: in scope. Maps to FR-008, FR-009, FR-017, and FR-019.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST represent Claude surfaces as durable surface bindings attached to one canonical managed session.
- **FR-002**: A surface binding MUST expose a surface identifier, surface kind, projection mode, connection state, interactivity flag, optional capabilities, and optional last-seen timestamp.
- **FR-003**: A session MUST allow at most one primary surface binding at a time.
- **FR-004**: A session MAY have multiple remote projection bindings.
- **FR-005**: Remote Control projection MUST add or update a surface binding without changing `session_id` or `execution_owner`.
- **FR-006**: Remote Control projection MUST use projection mode `remote_projection`.
- **FR-007**: Cloud handoff MUST create a distinct destination session rather than mutating the source session.
- **FR-008**: A cloud handoff destination MUST use `execution_owner = anthropic_cloud_vm`, projection mode `handoff`, and `handoff_from_session_id` pointing at the source session.
- **FR-009**: A cloud handoff destination MUST carry bounded handoff seed artifact references when handoff seed artifacts are provided.
- **FR-010**: Surface disconnect, reconnecting, connected, and detached states MUST be representable without forcing the session into failed state.
- **FR-011**: Resuming on a different surface MUST preserve canonical session identity when execution owner remains unchanged.
- **FR-012**: Resuming on a different surface MUST update the primary surface binding without creating a cloud handoff lineage edge.
- **FR-013**: Surface attachment and detachment operations MUST reject blank or unsupported surface identifiers and unsupported surface kinds.
- **FR-014**: Runtime records MUST distinguish local execution, Remote Control projection, and cloud execution without inferring execution location from UI surface alone.
- **FR-015**: Surface lifecycle events MUST use normalized names for attached, connected, disconnected, reconnecting, detached, resumed, and handoff-created events.
- **FR-016**: Surface lifecycle events MUST include bounded identity fields for the affected session and surface or handoff lineage.
- **FR-017**: Handoff events MUST identify source session, destination session, and seed artifact references without embedding large summary payloads.
- **FR-018**: Security and compliance classification MUST return distinct modes for local execution, Remote Control projection, and cloud execution.
- **FR-019**: Unsupported projection modes, lifecycle event names, or handoff shapes MUST fail validation instead of falling back silently.

### Key Entities

- **Claude Surface Binding**: Durable client attachment record for a Claude managed session, including surface identity, kind, projection mode, connection state, capabilities, and last-seen metadata.
- **Claude Surface Lifecycle Event**: Bounded normalized event describing surface attachment, connection, disconnection, reconnection, detachment, resume, or handoff.
- **Claude Handoff Lineage**: Source-to-destination relationship for cloud handoff, including source session, destination session, handoff type, and seed artifact references.
- **Claude Execution Security Classification**: Operator-visible classification that distinguishes local execution, Remote Control projection, and cloud execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove a session can hold exactly one primary surface and multiple projection surfaces while rejecting multiple primary bindings.
- **SC-002**: Unit tests prove Remote Control projection preserves `session_id` and `execution_owner`.
- **SC-003**: Unit tests prove surface disconnect/reconnect/detach states do not force a failed session state.
- **SC-004**: Unit tests prove cloud handoff creates a distinct `anthropic_cloud_vm` session with source lineage and bounded seed artifact references.
- **SC-005**: Unit tests prove local execution, Remote Control projection, and cloud execution produce distinct security classifications.
- **SC-006**: Integration-style boundary tests construct attach, disconnect, reconnect, resume, and cloud handoff flow with normalized events and no embedded large handoff payloads.
- **SC-007**: Validation evidence maps DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-024, and DESIGN-REQ-028 to passing behavior.
