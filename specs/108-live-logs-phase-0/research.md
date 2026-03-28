# Research: Live Logs Phase 0 Codebase Inventory

## 1. Documentation Review (`DOC-REQ-001`, `DOC-REQ-009`)

- `docs/ManagedAgents/LiveLogs.md` is confirmed as the target canonical blueprint for live log delivery.
- Legacy mentions of `tmate` / `web_ro`:
  - `docs/tmp/005-ProviderProfilesPlan.md`
  - `docs/ManagedAgents/OAuthTerminal.md`
  - `docs/ManagedAgents/SharedManagedAgentAbstractions.md`
  - `specs/084-live-log-tailing/` contains legacy tailing requirements that intersect with this design.
  - `docs/Temporal/LiveTaskManagement.md`

## 2. Inventory of Legacy Session References (`DOC-REQ-002`, `DOC-REQ-003`)

- Backend API Routes tracking session/tmate:
  - `api_service/api/schemas_task_runs.py`
  - `api_service/api/routers/task_runs.py`
  - `tests/unit/api/routers/test_task_runs.py`
- Worker Runtimes injecting tmate logic:
  - `moonmind/workflows/temporal/activity_runtime.py`
  - `moonmind/agents/codex_worker/worker.py`
  - `moonmind/workflows/temporal/runtime/strategies/base.py`
- Frontend surfaces:
  - `frontend/src/generated/openapi.ts` contains session definitions.

## 3. Data Models (`DOC-REQ-004`)

`api_service/db/models.py` contains:
- Schema mapped to TaskRunLiveSession mapping to the tmate session ID.
- `TaskRun` tables likely tracking process execution references.

## 4. Artifact Writing Paths (`DOC-REQ-005`)

- Stdout/stderr artifact persistence logic must be wired natively into `activity_runtime.py` replacing the legacy pass-through pipe via tmate wrappers.

## 5. Architectural Boundaries (`DOC-REQ-006`, `DOC-REQ-008`)

- **New boundary**: Standard task runs will dump artifacts asynchronously to durable backend blob stores (or S3 compatibility paths). The `api/routers/task_runs.py` will supply standard GET queries indexing off logs instead of returning an active session WebSocket URI.
- **Migration Fallback**: Any run operating with an active `TaskRunLiveSession` string will automatically fallback to routing the tmate URL, while null `session` paths query log artifacts.

## 6. Implementation Feature Flags (`DOC-REQ-007`)

- We must track `logStreamingEnabled` natively in the backend config API environment schemas (`api_service/core/config.py`).
