# MM-347 MoonSpec Orchestration Input

## Source

- Jira issue: MM-347
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-006: Model Claude subagents and agent teams separately
- Labels: none
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

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
