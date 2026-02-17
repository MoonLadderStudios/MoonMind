# Data Model: Agent Queue Task Cancellation

## Entity: AgentJob (existing)

### Added Fields
- `cancel_requested_at`: nullable timestamp (timezone-aware)
- `cancel_requested_by_user_id`: nullable UUID FK to `user.id`
- `cancel_reason`: nullable text

### Existing Fields Used by Cancellation
- `status`: enum includes `queued`, `running`, `cancelled`, etc.
- `claimed_by`: worker ownership marker for running jobs
- `lease_expires_at`: lease expiry used by requeue normalization
- `finished_at`: terminal completion timestamp

### State Transition Rules
- `queued -> cancelled` via cancel request endpoint.
- `running -> cancelled` via cancel ack endpoint (worker-owned path only).
- Running cancel request does not change status; it sets cancellation-request metadata.
- Cancellation-requested jobs are terminalized as `cancelled` in lease-expiry and retry handling paths.

## Entity: JobModel / API DTO (existing)

### Added Serialized Properties
- `cancelRequestedAt`
- `cancelRequestedByUserId`
- `cancelReason`

These fields must be present in REST and MCP queue responses where `JobModel` is used.

## Entity: AgentJobEvent (existing)

### Cancellation Event Types (message-level semantics)
- `Cancellation requested`
- `Job cancelled`
- Optional worker cancellation stop notices for stage/event logging

Events remain append-only and auditable.
