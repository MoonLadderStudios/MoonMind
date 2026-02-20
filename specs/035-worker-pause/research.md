# Research: Worker Pause System

## Decision 1: Persist pause state inside the queue schema with transactional guards
- **Decision**: Define SQLAlchemy models `SystemWorkerPauseState` (singleton `id=1`) and `SystemControlEvent` inside `moonmind/workflows/agent_queue/models.py`, create them via a dedicated Alembic migration, and always access/modify the singleton through repository helpers that run `SELECT ... FOR UPDATE` before mutating. Seed the row during migration to avoid “missing singleton” branches.
- **Rationale**: The queue service already owns the `Base` metadata and async sessions used by claim/heartbeat paths, so colocating pause state keeps toggles atomic with audit rows and ensures claim guards see a consistent snapshot. Locking the row avoids interleaving pause/resume requests (DOC-REQ-001/002) and lets us increment `version` safely for worker diffing.
- **Alternatives**:
  - Reuse a generic `settings` table in `api_service/db/models.py`. Rejected because claim guards run in `moonmind.workflows.agent_queue.service.AgentQueueService`, which expects queue-specific repositories and would still need the same singleton logic.
  - Store pause state in Redis. Rejected because docs require an auditable DB record with transactional integrity and version numbers that survive restarts.

## Decision 2: Guard claims in the service layer with a shared `QueueSystemMetadata`
- **Decision**: Add a dataclass/Pydantic model describing `{ workersPaused, mode, reason, version, requestedAt, updatedAt }` plus derived metrics. `AgentQueueService.claim_job` fetches this snapshot before calling the repository, returns `(job, metadata)`, and short-circuits when paused so `_requeue_expired_jobs` is never invoked (DOC-REQ-004). `heartbeat`, `job` serializers, and MCP tools all reuse the same model so every client sees identical `system` payloads.
- **Rationale**: The service layer is the single ingress for HTTP, MCP, and potential gRPC clients, so placing the guard there guarantees every claim obeys the doc requirements without duplicating DB reads in each transport. Returning metadata even when workersPaused=false lets workers detect version bumps (log once per version) and heartbeat watchers honor quiesce instructions (DOC-REQ-005/007).
- **Alternatives**:
  - Gate inside FastAPI routers only. Rejected because MCP invokes service methods directly and would bypass the guard.
  - Gate inside the repository by skipping `_requeue_expired_jobs`. Rejected because the repository should remain persistence-focused and unaware of HTTP semantics such as reason validation or audit fan-out; the service already manages events and metrics.

## Decision 3: Derive drain metrics via targeted COUNT queries
- **Decision**: Augment `AgentQueueRepository` with helper methods that compute `queuedCount`, `runningCount`, and `staleRunningCount` using `SELECT COUNT(*)` filters on `agent_jobs` without locking rows or invoking `_requeue_expired_jobs`. Treat `staleRunning` as running rows whose `lease_expires_at` is past due or NULL. Set `isDrained = runningCount == 0 && staleRunningCount == 0`.
- **Rationale**: Operators need drain telemetry (DOC-REQ-006/010) while the system is paused, but metrics collection must not mutate queue state. COUNT queries on the existing indexes are cheap, compatible with SQLite test fixtures, and retain accuracy within ±1 job thanks to queue timestamps refreshed on status changes.
- **Alternatives**:
  - Materialize a view/summary table. Rejected for v1 because it adds migration + write complexity without strong benefits at current scale.
  - Query via ORM relationships or load entire job lists. Rejected because it would add O(N) memory pressure during pauses when jobs might be large.

## Decision 4: Worker + MCP propagation strategy
- **Decision**: Update `QueueApiClient.claim_job` / `.heartbeat` to return `(job, system)` objects, extend `CodexWorker` with `pause_poll_interval_ms` + `last_pause_version_logged`, and set `_active_pause_event` whenever heartbeat metadata reports `mode="quiesce"`. Mirror the JSON in `moonmind/mcp/tool_registry.QueueToolRegistry` so IDE/CLI integrations receive the same envelope.
- **Rationale**: Workers already loop inside `CodexWorker.run_forever`, so centralizing the logic there lets us insert pause-aware sleep/backoff without touching every handler. `pause_poll_interval_ms` keeps API load low while paused, and reusing `system` metadata in both HTTP + MCP flows satisfies DOC-REQ-005/007/009/011.
- **Alternatives**:
  - Handle pause in each worker handler (`CodexExecHandler`, etc.). Rejected because it would scatter identical checks across dozens of call sites and risk inconsistencies.
  - Expose a separate “status” endpoint for workers to poll. Rejected to minimize round trips; piggybacking on claim/heartbeat responses keeps compatibility with existing firewalls and simplifies telemetry.
