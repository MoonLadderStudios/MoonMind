Result: this needs clarification before it can become actionable backlog work.

I reviewed the issue against the current codebase. MoonMind already has several state/control surfaces today:

| Area | Evidence |
| --- | --- |
| Execution lifecycle and state | `GET /api/executions`, `GET /api/executions/{workflowId}`, update, signal, cancel, rerun, facets, and step-ledger routes in `api_service/api/routers/executions.py`; documented in `docs/Tasks/TaskRunsApi.md`. |
| Active job observability | `/api/task-runs/{taskRunId}/observability-summary`, structured observability events, logs, diagnostics, and live-follow support in `api_service/api/routers/task_runs.py`; documented in `docs/Tasks/TaskRunsApi.md`. |
| Operator UI | README describes Mission Control as the current surface for real-time progress, step progress, logs, diagnostics, artifacts, interventions, and execution history. |
| MCP/tool direction | `/mcp/tools` and `/mcp/tools/call` exist, but the legacy queue registry is currently a stub and the issue does not specify which MoonMind-owned conversational/tool surface should be expanded. |

The remaining request is still too broad to create a good Jira story. It says there should be “better methods of talking to MoonMind,” “a method of talking to the orchestrator,” and “possibly” better tools for active jobs, but it does not define the desired user, transport, operations, or acceptance criteria.

Could you clarify the intended surface and scope?

1. Should this be a human-facing chat/CLI, an MCP tool set for agents, REST endpoints, Mission Control UI, or something else?
2. What deployment-level questions must it answer, for example service health, Temporal worker health, queue/backlog state, configured providers, active sessions, or recent failures?
3. What active-job operations are expected, for example inspect, follow logs, pause, resume, cancel, approve, retry, attach context, or send feedback?
4. Should this target the current Temporal execution model rather than the removed legacy `mm-orchestrator` runtime?

Once those are answered, this can be split into a concrete story with acceptance criteria instead of guessing at the wrong interface.

Tests not run: no product code was changed; this was a triage review only.
