# Contract: Claude Child Work

Source story: MM-347 / STORY-006.

## Python Schema Surface

The managed-session schema boundary must export the following names from `moonmind.schemas.managed_session_models` and `moonmind.schemas`:

- `CLAUDE_CHILD_WORK_EVENT_NAMES`
- `ClaudeChildContext`
- `ClaudeChildContextCommunication`
- `ClaudeChildContextLifecycleOwner`
- `ClaudeChildContextReturnShape`
- `ClaudeChildContextStatus`
- `ClaudeChildWorkEvent`
- `ClaudeChildWorkEventName`
- `ClaudeChildWorkFixtureFlow`
- `ClaudeChildWorkUsage`
- `ClaudeSessionGroup`
- `ClaudeSessionGroupStatus`
- `ClaudeTeamMemberRole`
- `ClaudeTeamMemberSession`
- `ClaudeTeamMemberStatus`
- `ClaudeTeamMessage`
- `build_claude_child_work_fixture_flow`
- `validate_claude_team_message_membership`

## Subagent ChildContext Wire Shape

```json
{
  "childContextId": "child-subagent-1",
  "parentSessionId": "claude-session-parent",
  "parentTurnId": "turn-1",
  "profile": "researcher",
  "contextWindow": "isolated",
  "returnShape": "summary_plus_metadata",
  "communication": "caller_only",
  "lifecycleOwner": "parent_turn",
  "status": "completed",
  "usage": {
    "inputTokens": 1000,
    "outputTokens": 400,
    "totalTokens": 1400
  },
  "startedAt": "2026-04-16T00:00:00Z",
  "completedAt": "2026-04-16T00:01:00Z",
  "metadata": {
    "background": false
  }
}
```

Contract rules:
- `childContextId`, `parentSessionId`, `parentTurnId`, and `profile` are required and non-blank.
- `contextWindow` is fixed to `isolated`.
- `communication` is fixed to `caller_only`.
- `lifecycleOwner` is fixed to `parent_turn`.
- Child contexts must not include a top-level `sessionId`.
- Metadata must not include promotion fields such as `promotedSessionId`.

## Session Group Wire Shape

```json
{
  "sessionGroupId": "team-group-1",
  "leadSessionId": "claude-session-lead",
  "status": "completed",
  "usage": {
    "inputTokens": 1200,
    "outputTokens": 800,
    "totalTokens": 2000
  },
  "createdAt": "2026-04-16T00:00:00Z",
  "completedAt": "2026-04-16T00:02:00Z",
  "metadata": {}
}
```

Contract rules:
- `sessionGroupId` and `leadSessionId` are required and non-blank.
- A session group owns team lifecycle and group usage rollup.
- Metadata must remain compact.

## Team Member Wire Shape

```json
{
  "sessionId": "claude-session-teammate",
  "sessionGroupId": "team-group-1",
  "role": "teammate",
  "status": "completed",
  "usage": {
    "inputTokens": 600,
    "outputTokens": 300,
    "totalTokens": 900
  },
  "metadata": {}
}
```

Contract rules:
- Every team member is a distinct managed session identity.
- Roles are `lead` or `teammate`.
- Member usage remains separately inspectable from group usage.

## Team Message Wire Shape

```json
{
  "messageId": "message-1",
  "sessionGroupId": "team-group-1",
  "senderSessionId": "claude-session-lead",
  "peerSessionId": "claude-session-teammate",
  "sentAt": "2026-04-16T00:01:30Z",
  "metadata": {
    "messageRef": "artifact://messages/message-1"
  }
}
```

Contract rules:
- Sender and peer must differ.
- Boundary validation rejects messages unless sender and peer are both members of the same `sessionGroupId`.
- Message metadata remains bounded and may point to external artifacts for larger payloads.

## Event Contract

Allowed event names:
- `child.subagent.started`
- `child.subagent.completed`
- `team.group.created`
- `team.member.started`
- `team.message.sent`
- `team.member.completed`
- `team.group.completed`

Rules:
- Subagent events require `childContextId`.
- Team events require `sessionGroupId`.
- `team.message.sent` requires `peerSessionId`.
- All event metadata must pass compact Temporal mapping validation.

## Fixture Flow Helper Contract

`build_claude_child_work_fixture_flow(...)` accepts:
- parent session and turn identifiers
- child context, group, lead, teammate, and message identifiers
- `created_at` timestamp
- optional metadata

It returns `ClaudeChildWorkFixtureFlow` containing:
- parent `ClaudeManagedSession`
- completed `ClaudeChildContext`
- completed `ClaudeSessionGroup`
- lead and teammate `ClaudeTeamMemberSession` records
- one `ClaudeTeamMessage`
- normalized `ClaudeChildWorkEvent` records covering the subagent and team flow

The helper is deterministic, bounded, and provider-free so unit and integration tests can validate the runtime boundary without live Claude execution.
