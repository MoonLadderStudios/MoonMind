# MM-342 MoonSpec Orchestration Input

## Source

- Jira issue: MM-342
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-001: Define Claude managed-session core schema
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

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
