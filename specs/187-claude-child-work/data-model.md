# Data Model: Claude Child Work

## Claude ChildContext

Represents parent-owned subagent child work for one Claude managed-session turn.

Fields:
- `childContextId`: stable non-blank child context identifier.
- `parentSessionId`: canonical parent Claude managed-session identifier.
- `parentTurnId`: parent turn identifier that owns the child work.
- `profile`: bounded subagent profile or role name.
- `contextWindow`: must identify isolated subagent context.
- `returnShape`: summary or summary-plus-metadata.
- `communication`: caller-only.
- `lifecycleOwner`: parent-turn.
- `status`: lifecycle state.
- `usage`: optional child usage summary.
- `startedAt`, `completedAt`: lifecycle timestamps.
- `metadata`: compact, bounded diagnostics.

Validation:
- A child context must not carry a top-level peer session identifier.
- Background subagents remain parent-owned and cannot declare promotion to a sibling session.
- Completed timestamps cannot precede started timestamps.
- Metadata must remain compact and Temporal-safe.

## Claude SessionGroup

Represents an agent-team group containing sibling managed sessions.

Fields:
- `sessionGroupId`: stable non-blank group identifier.
- `leadSessionId`: managed session identifier for the lead session.
- `status`: group lifecycle state.
- `createdAt`, `completedAt`: lifecycle timestamps.
- `usage`: optional group usage summary.
- `metadata`: compact, bounded diagnostics.

Validation:
- Group identifiers and lead identifiers must be non-blank.
- Completed timestamps cannot precede created timestamps.
- Metadata must remain compact and Temporal-safe.

## Claude TeamMemberSession

Represents one managed session participating in a Claude agent team.

Fields:
- `sessionId`: distinct managed session identifier.
- `sessionGroupId`: shared team group identifier.
- `role`: lead or teammate.
- `status`: member lifecycle state.
- `usage`: optional per-member usage summary.
- `metadata`: compact, bounded diagnostics.

Validation:
- The member must reference a session group.
- Role values reject unknown inputs.
- Metadata must remain compact and Temporal-safe.

## Claude TeamMessage

Represents direct peer communication inside one agent team.

Fields:
- `messageId`: stable non-blank message identifier.
- `sessionGroupId`: team group identifier.
- `senderSessionId`: managed session identifier for the sender.
- `peerSessionId`: managed session identifier for the recipient.
- `sentAt`: timestamp.
- `metadata`: compact, bounded message metadata.

Validation:
- Sender and peer must be different sessions.
- Boundary helpers must reject messages where sender and peer are not members of the same session group.
- Metadata must remain compact and Temporal-safe.

## Claude ChildWorkUsage

Represents bounded token or work accounting for child work.

Fields:
- `inputTokens`: non-negative input token count.
- `outputTokens`: non-negative output token count.
- `totalTokens`: non-negative total token count.
- `metadata`: compact, bounded diagnostics.

Validation:
- `totalTokens` must be greater than or equal to `inputTokens + outputTokens`.
- Metadata must remain compact and Temporal-safe.

## Claude ChildWorkEvent

Represents normalized events for subagent and team lifecycle.

Fields:
- `eventId`: stable non-blank event identifier.
- `sessionId`: related session identifier.
- `turnId`: optional turn identifier.
- `childContextId`: optional child context identifier.
- `sessionGroupId`: optional session group identifier.
- `peerSessionId`: optional peer session identifier.
- `eventName`: documented child-work event name.
- `occurredAt`: event timestamp.
- `metadata`: compact, bounded event metadata.

Allowed event names:
- `child.subagent.started`
- `child.subagent.completed`
- `team.group.created`
- `team.member.started`
- `team.message.sent`
- `team.member.completed`
- `team.group.completed`

Validation:
- Subagent events require a child context identifier.
- Team events require a session group identifier.
- Message events require a peer session identifier.
- Metadata must remain compact and Temporal-safe.

## Claude ChildWorkFixtureFlow

Helper-produced deterministic result that carries:
- parent session
- subagent child context
- session group
- team member sessions
- team message
- child-work events

State transitions:
- Subagent starts and completes under the parent turn.
- Team group is created, members start, peer message is sent, members complete, and group completes.
- Usage remains separately inspectable for subagent, team members, and team group.
