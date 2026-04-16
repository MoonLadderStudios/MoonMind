# Feature Specification: Claude Child Work

**Feature Branch**: `187-claude-child-work`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-347 from MM board
Summary: MoonSpec STORY-006: Model Claude subagents and agent teams separately
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-347 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-347: MoonSpec STORY-006: Model Claude subagents and agent teams separately

User Story
As a workflow designer, I need Claude subagents and agent teams modeled as distinct child-work primitives so parent-owned child contexts are not confused with peer sessions that communicate directly.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 15. Child work model: subagents and teams
- 17.6 Child-work APIs
- 18.7 Child-work events
- 24. Open questions

Coverage IDs
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-028
- DESIGN-REQ-030

Story Metadata
- Story ID: STORY-006
- Short name: claude-child-work
- Dependency mode: none
- Story dependencies from breakdown: STORY-001

Acceptance Criteria
- Subagents receive child_context_id, parent session and turn ids, isolated context, summary return shape, and parent-owned lifecycle.
- Subagent usage rolls up to the parent session while preserving child metadata.
- Agent-team lead and teammates each have ManagedSession records under a shared session_group_id.
- Team messaging emits normalized peer message events and supports group-aware teardown.
- The model does not collapse subagents and teammates into a single abstraction.

Requirements
- Distinguish child contexts from grouped sibling sessions.
- Expose SpawnSubagent, CreateTeamTeammate, and SendTeamMessage contracts.
- Preserve usage, resume, archive, lineage, communication, and surface semantics for each child-work shape.

Independent Test
Spawn a subagent and an agent-team teammate from controlled fixtures, then assert the subagent has no top-level peer session by default while teammates are separate ManagedSession records in a SessionGroup with distinct usage and events.

Implementation Notes
- Model Claude subagents as parent-owned child contexts with `child_context_id`, isolated context windows, summary-only returns, caller-only communication, and parent-turn lifecycle ownership.
- Model Claude agent teams as grouped sibling `ManagedSession` records under a shared `session_group_id`, with direct peer messaging and group-aware lifecycle handling.
- Keep subagent token accounting rolled up to the parent session while preserving child metadata; keep team usage separable per session with group rollups.
- Add or update contracts for `SpawnSubagent(session_id, turn_id, profile, prompt)`, `CreateTeamTeammate(session_group_id, config)`, and `SendTeamMessage(session_id, peer_session_id, payload)`.
- Emit normalized child-work events for subagents and teams, including `child.subagent.started`, `child.subagent.completed`, `team.group.created`, `team.member.started`, `team.message.sent`, `team.member.completed`, and `team.group.completed`.
- Do not collapse subagents and teammates into a single abstraction; preserve separate communication, resume, archive, lineage, usage, and surface attachment semantics.
- Treat the long-running background subagent promotion question as unresolved unless the spec explicitly resolves it.

Out of Scope
- Remote Control projection
- Enterprise telemetry dashboards
- Deciding schedule-template promotion

Source Design Coverage
- DESIGN-REQ-017: Covered by modeling subagents as child contexts with parent-owned lifecycle and child metadata.
- DESIGN-REQ-018: Covered by modeling agent teams as grouped sibling ManagedSession records with a shared session_group_id.
- DESIGN-REQ-019: Covered by separate usage accounting and rollup semantics for subagents and teams.
- DESIGN-REQ-020: Covered by preserving lineage, resume, archive, communication, and surface semantics per child-work shape.
- DESIGN-REQ-028: Covered by normalized event emission for subagent and team lifecycle events.
- DESIGN-REQ-030: Covered by preserving open clarification around whether background subagents can ever promote to sibling sessions.

Needs Clarification
- [NEEDS CLARIFICATION] Should a long-running background subagent ever promote to a sibling session?
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Child Work

**Summary**: As a workflow designer, I want Claude subagents and agent teams represented as distinct child-work primitives so that parent-owned child contexts are not confused with peer sessions that communicate directly.

**Goal**: Workflow designers and operators can inspect Claude child work and reliably distinguish a subagent child context from an agent-team teammate session across identity, lineage, lifecycle ownership, communication, usage accounting, and events.

**Independent Test**: Spawn a subagent and an agent-team teammate from controlled fixtures, then assert that the subagent has no top-level peer session by default while teammates are separate managed session records in a session group with distinct usage and events.

**Acceptance Scenarios**:

1. **Given** a Claude parent session starts a subagent, **when** the child work is recorded, **then** the child receives a child context identifier, parent session and turn identifiers, isolated context, summary return metadata, and parent-owned lifecycle metadata.
2. **Given** a Claude subagent produces usage, **when** usage is summarized, **then** the usage rolls up to the parent session while preserving child metadata for inspection.
3. **Given** a Claude agent team is created, **when** the lead and teammates are recorded, **then** every participant is represented as a separate managed session under a shared session group identifier.
4. **Given** team participants communicate, **when** a peer message is sent, **then** the system records a normalized peer message event that identifies the sending session, receiving peer session, and session group.
5. **Given** child work ends or is torn down, **when** operators inspect lifecycle state, **then** team teardown is group-aware and subagent teardown remains parent-turn-owned.
6. **Given** child work metadata is inspected, **when** subagents and teammates are compared, **then** the system never represents them through one collapsed abstraction that hides identity, communication, lifecycle, usage, or surface attachment differences.

### Edge Cases

- A background subagent remains parent-owned and does not silently promote to a sibling session.
- A team message cannot be recorded unless both sender and peer are members of the same session group.
- A subagent output summary is returned for parent-turn consumption and is not treated as a generic peer-session message.
- Usage rollups must not double-count child usage when both child and parent summaries are inspected.
- Unknown child-work kinds or event names fail validation instead of being stored as generic session metadata.

## Assumptions

- MM-347 builds on the canonical Claude managed-session records introduced by STORY-001 and does not introduce a parallel session model.
- Long-running background subagents remain parent-owned for this story; automatic promotion to sibling sessions stays out of scope until explicitly specified.
- Runtime validation can use controlled fixtures for subagent and team behavior before live Claude provider child-work integration is available.

## Source Design Requirements

- **DESIGN-REQ-017**: Source `docs/ManagedAgents/ClaudeCodeManagedSessions.md` section 15 requires Claude subagents to be modeled as child contexts with parent-owned lifecycle and child metadata. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-005.
- **DESIGN-REQ-018**: Source section 15 requires agent teams to be modeled as grouped sibling sessions under a shared session group. Scope: in scope. Maps to FR-006, FR-007, FR-008, and FR-009.
- **DESIGN-REQ-019**: Source section 15 requires subagent usage to roll up to the parent session and team usage to remain separable per teammate with group rollups. Scope: in scope. Maps to FR-005 and FR-010.
- **DESIGN-REQ-020**: Source sections 15 and 17.6 require separate child-work contracts and semantics for lineage, resume, archive, communication, permissions, and surface attachment. Scope: in scope. Maps to FR-011, FR-012, FR-013, and FR-014.
- **DESIGN-REQ-028**: Source section 18.7 requires normalized child-work and team events. Scope: in scope. Maps to FR-015, FR-016, and FR-017.
- **DESIGN-REQ-030**: Source section 24 tracks whether long-running background subagents can ever promote to sibling sessions as an open question. Scope: in scope as a boundary. Maps to FR-018.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST represent Claude subagents as child contexts associated with one parent session and one parent turn.
- **FR-002**: A subagent child context MUST expose a child context identifier, parent session identifier, parent turn identifier, isolated context indicator, return shape, communication mode, lifecycle owner, and child metadata.
- **FR-003**: A subagent child context MUST NOT be represented as a top-level peer managed session by default.
- **FR-004**: Subagent output MUST be represented as a child-work result for parent-turn consumption with summary or summary-plus-metadata shape.
- **FR-005**: Subagent usage MUST roll up to the parent session while preserving inspectable child usage metadata.
- **FR-006**: System MUST represent Claude agent teams as grouped sibling managed sessions.
- **FR-007**: An agent team MUST expose a session group identifier shared by the lead and all teammate sessions.
- **FR-008**: Each agent-team lead and teammate MUST retain a distinct managed session identity.
- **FR-009**: Agent-team teardown and archival state MUST be group-aware and MUST NOT be treated as parent-turn-owned subagent teardown.
- **FR-010**: Agent-team usage MUST remain inspectable per session and roll up to the session group without being merged into a subagent usage model.
- **FR-011**: System MUST expose child-work operations for spawning a subagent, creating a team teammate, and sending a team message.
- **FR-012**: Child-work operation inputs MUST reject unsupported or ambiguous child-work kinds instead of coercing them into a generic session type.
- **FR-013**: Team messages MUST include sender session, peer session, group identity, and bounded message metadata.
- **FR-014**: Team messages MUST be rejected when the sender and peer are not in the same session group.
- **FR-015**: Subagent lifecycle MUST emit normalized child-work events for start and completion.
- **FR-016**: Agent-team lifecycle MUST emit normalized events for group creation, member start, member completion, and group completion.
- **FR-017**: Team messaging MUST emit a normalized peer message event.
- **FR-018**: Long-running background subagents MUST remain parent-owned and MUST NOT silently promote to sibling sessions.

### Key Entities

- **Claude Child Context**: Parent-owned child-work record for a subagent, including identity, parent linkage, context isolation, return shape, communication mode, lifecycle owner, and usage metadata.
- **Claude Agent Team**: Grouped sibling-session structure for collaborative Claude work, identified by a shared session group.
- **Claude Team Member Session**: Distinct managed session participating in an agent team as lead or teammate.
- **Claude Child-Work Event**: Normalized bounded event describing subagent lifecycle, team lifecycle, or team peer messaging.
- **Claude Child-Work Usage Summary**: Bounded usage metadata that preserves subagent child usage, parent rollup, per-team-member usage, and team group rollup semantics.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove at least one subagent record carries parent session and turn identifiers, isolated context, return shape, lifecycle owner, and child usage rollup metadata.
- **SC-002**: Unit tests prove at least one agent-team lead and teammate are distinct managed sessions under one session group.
- **SC-003**: Unit tests prove unsupported child-work kinds and invalid cross-group team messages are rejected.
- **SC-004**: Boundary tests prove subagent start/completion, team group/member lifecycle, and peer message events use normalized event names and bounded metadata.
- **SC-005**: Integration-style tests construct a representative parent session, subagent child context, session group, teammate session, peer message, and teardown flow while preserving distinct identities and usage rollups.
- **SC-006**: Validation evidence maps DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-028, and DESIGN-REQ-030 to passing behavior.
