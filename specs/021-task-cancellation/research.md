# Research: Agent Queue Task Cancellation

## Decision 1: Use cooperative cancellation metadata for running jobs
- Decision: Add `cancel_requested_at`, `cancel_requested_by_user_id`, and `cancel_reason` to queue jobs and keep `status=running` until worker ack.
- Rationale: Current queue mutation helpers require worker ownership/state checks for running transitions; cooperative metadata avoids race-prone direct terminal flips.
- Alternatives considered:
  - Immediate `running -> cancelled` from user endpoint: rejected because it bypasses worker-owned transition model.
  - New intermediate `cancelling` status: rejected for larger state-table and compatibility churn.

## Decision 2: Keep cancellation endpoints idempotent
- Decision: `POST /cancel` and `POST /cancel/ack` return current job when already cancelled/requested where policy allows.
- Rationale: Repeated UI/MCP/worker retries must be safe and deterministic.
- Alternatives considered:
  - Strict 409 on repeated requests: rejected because retried requests are common in distributed worker flows.

## Decision 3: Prevent retry/requeue resurrection
- Decision: In repository retry and lease-expiry paths, cancellation-requested jobs finalize to `cancelled` instead of queueing another attempt.
- Rationale: Cancellation intent must dominate retry/backoff logic.
- Alternatives considered:
  - Keep existing retry behavior after cancel request: rejected due to zombie retry risk.

## Decision 4: Drive worker cancellation from heartbeat response
- Decision: Queue heartbeat returns full `JobModel`; worker reads `cancelRequestedAt` and sets a local cancel event checked at stage boundaries.
- Rationale: Reuses existing heartbeat control channel with no new polling endpoint.
- Alternatives considered:
  - Dedicated cancel polling endpoint: rejected as unnecessary additional API surface.

## Decision 5: Add best-effort subprocess interruption
- Decision: Command runner supports `cancel_event` and performs terminate then kill fallback when cancellation arrives.
- Rationale: Running task cancellation must interrupt long-lived subprocess workloads realistically.
- Alternatives considered:
  - Stage-boundary-only checks without process interruption: rejected as too slow/ineffective for long CLI runs.

## Decision 6: Keep dashboard integration minimal and source-config driven
- Decision: Expose queue cancel endpoint via dashboard runtime config and render a cancel button on queue detail with cancellation-request indicator.
- Rationale: Maintains current dashboard architecture and avoids heavy UI refactor.
- Alternatives considered:
  - New dedicated cancellation page: rejected as unnecessary complexity for MVP behavior.
