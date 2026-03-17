# Spec Removal Review

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-16  

## Purpose

Review of all spec directories that referenced `docs/TaskQueueSystem.md` (now replaced by `docs/Tasks/TaskRunsApi.md`) to determine whether each spec's underlying functionality still exists in the codebase or has been fully removed.

## Criteria

Specs are **living documents** — a fully-implemented spec still has value because it can be updated as the feature evolves. A spec should only be removed if the functionality it describes **no longer exists in the codebase** (i.e., the code was deleted or replaced by an entirely different system).

---

## Spec-by-Spec Assessment

| Spec | Functionality Still Exists? | Verdict |
|------|-----------------------------|---------|
| `009-agent-queue-mvp` | ✅ Yes | **Keep** |
| `010-agent-queue-artifacts` | ✅ Yes | **Keep** |
| `012-mcp-queue-tools-wrapper` | ✅ Yes | **Keep** |
| `013-queue-hardening-quality` | ✅ Yes | **Keep** |
| `015-skills-workflow` | ❌ No | **Remove** |
| `028-task-presets` | ✅ Yes | **Keep** |
| `035-pr-title-description` | ✅ Yes | **Keep** |
| `037-task-proposal-update` | ✅ Yes | **Keep** |
| `043-tasks-image-phase1` | ✅ Yes | **Keep** |
| `047-spec-removal` | ❌ No | **Remove** |
| `051-task-editing-system` | ✅ Yes | **Keep** |
| `053-resubmit-terminal-tasks` | ✅ Yes | **Keep** |
| `task/20260301/dcb0d51c-multi` | ❌ No (duplicate) | **Remove** |

---

## Details for Specs Recommended for Removal

### `015-skills-workflow` — Remove

This spec targeted the Celery-era Agentkit workflow stages (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`) and files in `moonmind/workflows/agentkit_celery/`. The source code for that module has been entirely deleted — only stale `__pycache__` bytecode files remain. The Temporal workflow system (`moonmind/workflows/temporal/`) replaced this functionality entirely.

### `047-spec-removal` — Remove

This spec was about migrating legacy "Spec Automation" naming to canonical workflow terminology. Its incomplete tasks heavily target removed files (`agentkit_celery/models.py`, `agentkit_celery/storage.py`, `agentkit_celery/tasks.py`, `agentkit_celery/serializers.py`). Since the Celery workflow code no longer exists, the migration scope is moot. If canonical naming cleanup is still desired, it would need a fresh spec scoped against the current Temporal codebase.

### `task/20260301/dcb0d51c-multi` — Remove

Exact duplicate of `047-spec-removal/tasks.md` placed in the task-run workspace directory. Should be removed regardless.

---

## Details for Specs to Keep

### `009-agent-queue-mvp`
Queue job table, CRUD, claim/heartbeat/complete/fail lifecycle — still the core of `/api/queue/jobs` in `agent_queue.py` and `AgentQueueService`.

### `010-agent-queue-artifacts`
Artifact upload, download, list, path traversal protection, size limits — still active in `agent_queue.py` artifact endpoints.

### `012-mcp-queue-tools-wrapper`
MCP tools wrapper (`mcp_tools.py`, `tool_registry.py`) — still present and in use.

### `013-queue-hardening-quality`
Worker token auth, capability matching, retry/dead-letter, SSE events — all still active in `agent_queue.py` and `AgentQueueService`.

### `028-task-presets`
Task step template catalog — still active in `task_step_templates.py` router, `task_templates/` services, and the dashboard.

### `035-pr-title-description`
PR title/body override precedence and deterministic defaults — still active in the publish path.

### `037-task-proposal-update`
`proposalPolicy` targeting, MoonMind CI routing, severity gating — still active in `task_contract.py` and `task_proposals/` services.

### `043-tasks-image-phase1`
Image attachment ingestion, validation, worker prepare, vision context — still active in attachment endpoints and worker flows.

### `051-task-editing-system`
In-place queued task editing with optimistic concurrency — still active in `agent_queue.py` PUT endpoint.

### `053-resubmit-terminal-tasks`
Terminal task resubmit flow — still active in `agent_queue.py` resubmit endpoint.
