# Research: Claude Child Work

## Runtime Boundary

Decision: Implement MM-347 as Pydantic schema contracts and deterministic helper functions in `moonmind/schemas/managed_session_models.py`.

Rationale: Existing Claude stories for session core, policy envelopes, decisions, hooks, and context snapshots already use this module as the compact managed-session schema boundary. Child work needs the same importable, Temporal-safe validation behavior before workflow or adapter persistence can consume it.

Alternatives considered: A separate `claude_child_work_models.py` module was considered, but it would split closely related Claude session-plane records and require extra exports before there is enough complexity to justify another module.

## Subagent Identity

Decision: Model subagents as `ClaudeChildContext` records with a `childContextId`, parent session and turn identifiers, isolated context, summary return shape, caller-only communication, parent-turn lifecycle ownership, and child usage metadata.

Rationale: The source design says subagents are parent-owned child contexts, not top-level peer sessions by default. A dedicated model prevents accidental use of `ClaudeManagedSession` for subagents and keeps resume, archive, usage, and communication semantics distinct.

Alternatives considered: Reusing `ClaudeManagedSession` with `parentSessionId` was rejected because it collapses subagents and teammates into one abstraction and violates MM-347.

## Team Identity

Decision: Model agent teams as `ClaudeSessionGroup` plus `ClaudeTeamMemberSession` records that reference distinct managed session identifiers under one `sessionGroupId`.

Rationale: The source design says team leads and teammates are grouped sibling sessions with direct peer messaging and group-aware teardown. Dedicated group/member records make identity and lifecycle validation explicit.

Alternatives considered: Encoding team membership only in `ClaudeManagedSession.extensions` was rejected because it hides a core runtime primitive in untyped metadata.

## Peer Messaging

Decision: Represent team peer messages as bounded `ClaudeTeamMessage` records and reject messages unless sender and peer session identifiers are distinct members of the same session group.

Rationale: Team communication is direct peer messaging, unlike subagent caller-only communication. Validation must prevent cross-group or self-message records from being accepted as normal team messages.

Alternatives considered: Free-form event metadata was rejected because it would make invalid team topology hard to detect.

## Usage Accounting

Decision: Use separate usage summary models for subagent child usage and team group usage while sharing a compact token-count structure.

Rationale: The story requires subagent usage to roll up to the parent session and team usage to remain inspectable per member with group rollups. A shared token-count structure avoids duplication without merging the semantics.

Alternatives considered: One generic usage blob was rejected because it would not prove the accounting models remain distinct.

## Event Surface

Decision: Export a strict child-work event-name tuple and `ClaudeChildWorkEvent` model for subagent start/completion, team group/member lifecycle, and team peer messaging.

Rationale: The acceptance criteria require normalized events and bounded metadata. Strict literals keep unsupported event names from entering workflow history as arbitrary strings.

Alternatives considered: Reusing context or hook event models was rejected because child-work events are a separate runtime surface.

## Background Subagents

Decision: Keep long-running background subagents parent-owned for MM-347 and reject automatic promotion metadata on child contexts.

Rationale: Jira carried this topic as a clarification. The non-interactive runtime default resolves it as a scope boundary so implementation can proceed: promotion remains out of scope and no silent semantic change is allowed.

Alternatives considered: Adding a promotion target field was rejected because it would imply behavior that the source design leaves unresolved.

## Testing Strategy

Decision: Add focused unit tests for model validation, topology validation, usage rollup semantics, event names, and invalid child-work inputs; add an integration-style schema boundary test that builds a representative parent session, subagent, team group, teammate, peer message, events, usage summaries, and teardown state.

Rationale: This matches the existing Claude schema story pattern and gives workflows/adapters a stable contract before persistence or live provider wiring is added.

Alternatives considered: Full Temporal workflow tests were rejected for this story because no workflow or activity signature changes are required; integration-style schema tests are sufficient for the current boundary.
