# Remaining work — Universal tmate OAuth and live sessions

**Canonical design:** [`docs/ManagedAgents/TmateArchitecture.md`](../../ManagedAgents/TmateArchitecture.md)  
**Implementation plan:** [`docs/tmp/050-TmatePlan.md`](../050-TmatePlan.md)  
**Session lifecycle:** [`docs/Temporal/TmateSessionArchitecture.md`](../../Temporal/TmateSessionArchitecture.md)

Open implementation and documentation alignment for tmate-backed OAuth sessions, managed-runtime wrapping, and Mission Control live output. Phase breakdown and file pointers are maintained in [`050-TmatePlan.md`](../050-TmatePlan.md); this file is a short index for backlinks from `TmateArchitecture.md`.

## Open items (summary)

| Area | Notes |
|------|--------|
| OAuth Temporal integration test | Full workflow lifecycle (create → tmate URLs → verify → register). See Phase **1.9** in `050-TmatePlan.md`. |
| OAuth `start_auth_runner` | Still Docker container + host `docker exec` polling; shared display keys only. Unify commands/config/timeouts with `TmateSessionManager`. Phase **2.2**. |
| Codex worker live tmate | `_ensure_live_session_started` duplicates launcher semantics without `TmateSessionManager`. Phase **2.7**. |
| RW terminal handoff | Grant API, audit, Mission Control “request terminal”, operator messages, pause/resume. Phase **5**; schema groundwork on `TaskRunLiveSession`. |
| Doc naming | Prefer **`task_run_live_sessions`** / **`GET /api/task-runs/.../live-session`** in canonical docs; retire `workflow_live_sessions` and workflow-scoped GET examples where they appear. |

## Related specs

- [`specs/024-live-task-handoff`](../../../specs/024-live-task-handoff/)
- [`specs/104-tmate-session-manager`](../../../specs/104-tmate-session-manager/)
