# Research: Worker Pause System

## Decision 1: Persist pause state via dedicated tables in queue schema
- **Decision**: Create two SQLAlchemy models inside `moonmind/workflows/agent_queue/models.py`: `SystemWorkerPauseState` (singleton row with id=1) and `SystemControlEvent` (append-only audit). Manage them through `AgentQueueRepository` helper methods so FastAPI, MCP, and workers share the same persistence path.
- **Rationale**: The queue service already owns the `agent_jobs` models and migrations, so colocating pause state keeps transactional updates near claim logic and ensures atomic guard rails. A singleton row lets claim handlers run `SELECT ... FOR UPDATE` cheaply when toggling state.
- **Alternatives**:
  - Repurpose settings/config tables in `api_service/db/models.py`, but that creates cross-package coupling and does not sit inside the queue session.
  - Store state in Redis; rejected because doc mandates auditable DB persistence with versioning.

## Decision 2: Guard claims in the service layer with snapshot metadata
- **Decision**: Extend `AgentQueueService.claim_job` to return a structured `ClaimJobResult` containing the optional job plus the latest `WorkerPauseStatus`. When `paused=true`, the method returns early (skipping repository `claim_job` and `_requeue_expired_jobs`) and surfaces the pause metadata so routers/workers can respond.
- **Rationale**: The service is the single ingress for both REST and MCP claim calls. Putting the guard here prevents duplicated conditionals and guarantees `_requeue_expired_jobs` is never invoked while paused.
- **Alternatives**:
  - Guard inside the FastAPI router only; rejected because MCP tools, future gRPC endpoints, and worker-side helpers would bypass the pause requirement.
  - Guard inside repository; rejected because repository should remain data-centric and unaware of HTTP semantics like audit reason validation.

## Decision 3: Surface pause metadata to workers via shared schema types
- **Decision**: Introduce `SystemWorkerPauseStatusModel` (Pydantic) reused by REST, MCP, and worker HTTP client layers. Claim and heartbeat responses embed a `system` object so runtimes see the same shape regardless of transport.
- **Rationale**: Centralizing the schema avoids divergence (e.g., queue.claim returning different field names than queue.heartbeat). Pydantic ensures alias-cased JSON matches docs, and workers can treat metadata uniformly (log once per `version`, choose poll interval, etc.).
- **Alternatives**:
  - Custom JSON dict assembly per endpoint; rejected due to duplication and higher drift risk when future metadata fields (like maintenance ETA) are added.

## Decision 4: Dashboard + MCP propagation strategy
- **Decision**: Reuse the existing dashboard JS bundle (`api_service/static/task_dashboard/dashboard.js`) to render a global status badge and Pause/Resume controls that hit the new `/api/system/worker-pause` endpoints. MCP tools already use the queue service, so they only need schema changes to forward `system` metadata without UI adjustments.
- **Rationale**: The dashboard already polls queue APIs for metrics; extending it avoids building a new frontend. MCP wrappers simply echo REST results because we expose `system` in the shared schema.
- **Alternatives**:
  - Build a new React view or CLI toggle; rejected for scope creep.
  - Bypass UI changes by relying on CLI toggles only; rejected because doc requires dashboard controls.
