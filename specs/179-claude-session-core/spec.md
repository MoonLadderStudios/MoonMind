# Feature Specification: Claude Session Core

**Feature Branch**: `179-claude-session-core`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-342 from MM board
Summary: MoonSpec STORY-001: Define Claude managed-session core schema
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-342 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-342: MoonSpec STORY-001: Define Claude managed-session core schema

User Story
As a platform operator, I need Claude Code runs represented as canonical ManagedSession, Turn, WorkItem, and lineage records so Claude sessions can enter the shared Managed Session Plane without forking the core model.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 1. Executive summary
- 2.1 Preserve shared abstractions
- 5. Shared abstractions retained from the Codex design
- 7. Runtime model
- 9. Canonical domain model
- 10. Lifecycle state machines
- 22. Compatibility strategy with the Codex plane

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-026
- DESIGN-REQ-027
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-001
- Short name: claude-session-core
- Dependency mode: none
- Story dependencies from breakdown: None

Acceptance Criteria
- A Claude session can be created with `runtime_family = claude_code` and a valid `execution_owner`, primary surface, projection mode, and lifecycle state.
- Remote Control projection data can be represented without changing `execution_owner`.
- Cloud handoff can be represented as a new session with lineage to the source session.
- Session, turn, work-item, and surface lifecycle transitions match the design state machines.
- Internal Claude managed-session contracts expose `session_id` naming and do not introduce `thread_id` or `child_thread` aliases.

Requirements
- Represent every Claude Code run as a canonical ManagedSession.
- Model `execution_owner`, `surface_kind`, and `projection_mode` as separate fields.
- Support the documented local, cloud, SDK, scheduled, Remote Control, and handoff session shapes at the data-contract level.
- Preserve shared interfaces while isolating Claude-only fields as extensions.
- Keep runtime-specific transport details outside shared plane records.
- Preserve shared Managed Session Plane abstractions instead of forking the core model for Claude Code.

Independent Test
Create local, cloud, SDK, scheduled, Remote Control, and handoff-shaped Claude sessions through the session-plane boundary and assert persisted records, lifecycle events, and lineage use the normalized schema without Codex thread aliases.

Out of Scope
- Policy resolution.
- Checkpoint restore behavior.
- Subagent and team orchestration.
- Telemetry export.

Source Design Coverage
- DESIGN-REQ-001: Owns canonical Claude Code representation as ManagedSession records.
- DESIGN-REQ-002: Owns shared abstraction preservation across ManagedSession, Turn, WorkItem, and lineage records.
- DESIGN-REQ-003: Owns data-contract support for documented Claude session shapes.
- DESIGN-REQ-005: Owns separation of `execution_owner`, `surface_kind`, and `projection_mode`.
- DESIGN-REQ-006: Owns keeping runtime transport details outside shared plane records.
- DESIGN-REQ-026: Owns lifecycle state-machine alignment for sessions, turns, work items, and surfaces.
- DESIGN-REQ-027: Owns Remote Control projection modeling without mutating execution ownership.
- DESIGN-REQ-028: Owns cloud handoff lineage and shared-plane naming without Codex `thread_id` or `child_thread` aliases.

Needs Clarification
- None

Notes
This story establishes the core Claude Code managed-session schema that later Claude session-plane work can build on without creating a parallel model.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Session Core Schema

**Summary**: As a platform operator, I want Claude Code runs represented by canonical managed-session records so that Claude sessions can enter the shared Managed Session Plane without forking the core model.

**Goal**: Operators and maintainers can create and inspect Claude Code session, turn, work-item, surface, and lineage records that use the shared Managed Session Plane vocabulary while preserving Claude-specific runtime distinctions as explicit extensions.

**Independent Test**: Create local, cloud, SDK, scheduled, Remote Control, and handoff-shaped Claude sessions through the session-plane boundary, then assert the persisted records, lifecycle events, and lineage use normalized `session_id`-based schema fields without Codex `thread_id` or `child_thread` aliases.

**Acceptance Scenarios**:

1. **Given** a Claude Code run starts from a local terminal, **when** the session-plane boundary creates the run record, **then** the record uses `runtime_family = claude_code`, a valid local execution owner, a primary surface, and a valid session lifecycle state.
2. **Given** a Claude Code run is projected through Remote Control, **when** a web or mobile surface is attached, **then** the same canonical session keeps its execution owner and records the attached surface as a remote projection.
3. **Given** a local Claude Code run is handed off to cloud execution, **when** the handoff is represented, **then** a new canonical session is created with cloud execution ownership and lineage back to the source session.
4. **Given** Claude session, turn, work-item, and surface records move through lifecycle states, **when** validation runs, **then** only the documented state-machine values are accepted.
5. **Given** Claude managed-session records are serialized for internal contracts, **when** the payloads are inspected, **then** they use `session_id` naming and do not include `thread_id` or `child_thread` aliases.

### Edge Cases

- A web or mobile surface can represent either Remote Control projection or cloud execution; the data contract must distinguish these without inferring execution semantics from surface kind alone.
- A cloud handoff must not mutate the source session's execution owner or silently reuse the same session identity.
- SDK-hosted and scheduled sessions still need valid execution-owner, primary-surface, projection, lifecycle, and lineage fields.
- Runtime-specific transport details may be present in adapter-local data but must not be required in shared session-plane records.
- Payloads using Codex `thread_id` or `child_thread` naming must be rejected at the Claude managed-session contract boundary instead of accepted as compatibility aliases.

## Assumptions

- This story establishes data contracts and boundary validation for Claude managed-session core records; later stories will implement policy resolution, checkpoint restore behavior, subagent/team orchestration, and telemetry export.
- The shared Managed Session Plane vocabulary remains authoritative for cross-runtime records, while Claude-specific distinctions are represented as explicit extension fields.
- Existing Codex session-plane behavior should not be renamed or broken by this story.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/ManagedAgents/ClaudeCodeManagedSessions.md` sections 1 and 3.1 require every Claude Code run to be represented as a canonical `ManagedSession`. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-002**: Source sections 2.1 and 5 require the Claude plane to preserve shared Managed Session Plane abstractions including `ManagedSession`, `Turn`, `WorkItem`, runtime adapters, artifact references, usage, and lineage. Scope: in scope. Maps to FR-001, FR-003, and FR-008.
- **DESIGN-REQ-003**: Source sections 7.2 and 9 require data-contract support for local, cloud, SDK-hosted, scheduled, Remote Control, and handoff-shaped sessions. Scope: in scope. Maps to FR-004 and FR-005.
- **DESIGN-REQ-005**: Source sections 2.2, 7.1, and 16 require `execution_owner`, `surface_kind`, and `projection_mode` to be modeled as separate fields. Scope: in scope. Maps to FR-004 and FR-006.
- **DESIGN-REQ-006**: Source sections 2.6 and 22.2 require Claude runtime transport details to remain adapter-specific extensions outside shared plane records. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-026**: Source sections 9 and 10 require session, turn, work-item, and surface lifecycle state values to match documented state machines. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-027**: Source sections 7.3 and 16.2 require Remote Control to be modeled as a surface projection that does not change execution ownership. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-028**: Source sections 7.3 and 22 require cloud handoff lineage and shared-plane `session_id` naming without Codex `thread_id` or `child_thread` aliases. Scope: in scope. Maps to FR-005 and FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a canonical Claude managed-session schema that identifies Claude Code runs with `runtime_family = claude_code`.
- **FR-002**: Claude managed-session records MUST include a stable `session_id`, execution owner, primary surface, projection mode, lifecycle state, creation metadata, update metadata, and optional lineage references.
- **FR-003**: Claude turn and work-item records MUST reference the canonical `session_id` and use shared Managed Session Plane concepts instead of Claude-only or Codex-only vocabulary.
- **FR-004**: The schema MUST validate documented session shapes for local interactive, local Remote Control projection, cloud interactive, cloud scheduled, desktop scheduled, and SDK-hosted sessions.
- **FR-005**: Cloud handoff representation MUST create a distinct destination session with cloud execution ownership and lineage to the source session.
- **FR-006**: Remote Control representation MUST add or validate a remote-projection surface binding without changing the session execution owner.
- **FR-007**: Session, turn, work-item, and surface lifecycle fields MUST accept only the documented lifecycle state values for the selected record type.
- **FR-008**: Shared session-plane records MUST isolate Claude-specific extension fields without requiring runtime transport details in the shared core schema.
- **FR-009**: Claude managed-session contract payloads MUST use `session_id` naming and MUST reject Codex `thread_id` or `child_thread` aliases.

### Key Entities

- **Claude Managed Session**: Canonical record for a Claude Code run, including runtime family, execution owner, primary surface, lifecycle state, surface bindings, metadata, and lineage references.
- **Claude Turn**: Bounded unit of user, scheduled, channel, SDK, or team-message input processed within a Claude managed session.
- **Claude Work Item**: Event-bearing unit emitted during a Claude turn, such as context loading, tool execution, hook execution, checkpoint creation, compaction, rewind, subagent work, or summary generation.
- **Surface Binding**: Durable representation of a terminal, IDE, desktop, web, mobile, scheduler, channel, or SDK surface attached to a session with an explicit projection mode.
- **Session Lineage**: Relationship fields that connect handoff, fork, parent, or grouped sessions without mutating source session identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation tests cover at least local terminal, local Remote Control projection, cloud interactive, cloud scheduled, desktop scheduled, SDK-hosted, and cloud handoff session shapes.
- **SC-002**: Lifecycle tests demonstrate accepted and rejected values for session, turn, work-item, and surface states.
- **SC-003**: Serialization tests prove Claude managed-session payloads include `session_id` and reject `thread_id` and `child_thread` aliases.
- **SC-004**: Boundary tests prove Remote Control does not change execution owner and cloud handoff creates a distinct destination session with lineage.
- **SC-005**: Source design coverage for DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-026, DESIGN-REQ-027, and DESIGN-REQ-028 maps to passing validation evidence.
