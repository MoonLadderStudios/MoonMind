# Research: Agent Queue Hardening and Quality (Milestone 5)

## Decision 1: Worker auth enforcement model

- **Decision**: Enforce worker mutation endpoints through a worker auth context that accepts either a dedicated worker token header (`X-MoonMind-Worker-Token`) or authenticated principal (OIDC/JWT path), with token-bound policy when token path is used.
- **Rationale**: This directly implements Milestone 5 security posture while preserving compatibility for deployments already using JWT auth.
- **Alternatives considered**:
  - Bearer token only: rejected because docs explicitly call out dedicated worker token header.
  - Worker token only: rejected because milestone includes OIDC/JWT enforcement option.

## Decision 2: Per-worker policy and capability matching

- **Decision**: Resolve worker policy into allowlists (repositories/job types) and capabilities, then apply filters during claim selection before a job transitions to running.
- **Rationale**: Claim-time filtering prevents unauthorized job ownership and avoids introducing producer-side hard failures for jobs that could be executed by another worker.
- **Alternatives considered**:
  - Enforce repository policy at enqueue time only: rejected because policy is worker-specific.
  - Reject claim requests without filtering: rejected because it causes noisy failures instead of deterministic assignment.

## Decision 3: Retry/backoff/dead-letter semantics

- **Decision**: Add `next_attempt_at` scheduling and `dead_letter` job status. Retryable failures requeue with exponential backoff; exhausted retries move to dead-letter.
- **Rationale**: This provides explicit delay control and terminal handling, satisfying Milestone 5 while retaining existing attempt counters.
- **Alternatives considered**:
  - Immediate requeue retries only: rejected because milestone requires backoff.
  - Reuse `failed` for exhausted retries: rejected because milestone explicitly asks for dead-letter behavior.

## Decision 4: Job events and streaming-ish logs

- **Decision**: Add an append-only `agent_job_events` table and queue API endpoints to append/list events with `after` cursor-style polling.
- **Rationale**: Poll-based incremental event retrieval is straightforward to operate and fulfills streaming-ish visibility without introducing SSE/WebSocket complexity now.
- **Alternatives considered**:
  - Only file artifact logs: rejected because cross-machine progress needs low-latency status visibility.
  - Full SSE stream transport now: rejected as extra protocol surface beyond Milestone 5 scope.
