# Single Execution Substrate Migration Plan

> **Goal:** Finish the move to Temporal-backed execution as the **only** real substrate. Remove legacy queue and system paths so MoonMind has one task model, one execution model, one observability model, and one action model.
>
> **Last updated:** 2026-03-22

---

## Why This Matters

The docs and codebase still treat Mission Control as transitional — merging queue, system, and Temporal sources with mixed-source views described as "a convenience rather than a true source of truth." That was useful during migration, but it now:

- **Weakens the "one control plane" story** — operators see multiple sources instead of a unified model.
- **Adds permanent UI and API complexity** — dashboard view model, routing, status maps, pagination, and compatibility adapters all branch on `source`.
- **Blocks doc simplification** — 6+ architecture docs describe mixed-source rules that only exist for migration.

The target state is clean: **Temporal owns execution truth. Period.**

---

## Current State Audit

### What's Already Done ✅
- Temporal is the execution engine for all new task submissions
- `MoonMind.Run` and `MoonMind.AgentRun` workflows handle managed + external agent execution
- `/api/executions` adapter surface exists with full CRUD + signal/cancel
- `TemporalExecutionRecord` projection row with sync metadata
- system code already deleted from `moonmind/workflows/system/` (only `__pycache__` remains)
- system DB tables dropped via migration `c1d2e3f4a5b6`
- External Runs tab removed from dashboard
- system submissions rejected with error in `dashboard.js`

### What Still References Legacy Sources ⚠️

#### Python Backend
| Component | File | Legacy Reference |
|-----------|------|-----------------|
| View model | [task_dashboard_view_model.py](../../api_service/api/routers/task_dashboard_view_model.py) | Residual queue-oriented **system** keys (`defaultQueue`, `queueEnv`, `taskSourceResolver`); imports from `agent_queue.runtime_defaults` for submit defaults |
| Task compatibility router | [task_compatibility.py](../../api_service/api/routers/task_compatibility.py) | `source` filter accepts `queue` literal, `source_hint` accepts `queue` |
| Queue router | [agent_queue.py](../../api_service/api/routers/agent_queue.py) | Full queue API: `/api/queue/jobs`, `/api/tasks`, etc. |
| Agent queue module | [moonmind/workflows/agent_queue/](../../moonmind/workflows/agent_queue/) | `service.py` (112 KB), `repositories.py` (50 KB), `models.py`, `task_contract.py` (48 KB), etc. |
| Task routing | [routing.py](../../moonmind/workflows/tasks/routing.py) | References to system |
| Automation system | [system.py](../../moonmind/workflows/automation/system.py) | Automation-level orchestration code |
| Settings | [settings.py](../../moonmind/config/settings.py) | Queue/system config entries |

#### Frontend (dashboard.js)
| Area | Legacy Reference |
|------|-----------------|
| Route matching | Legacy `/tasks/system` removed; worker controls live at `/tasks/workers` |
| Submit form | `validatesystemSubmission`, `normalizesystemPriority`, `showsystemFields` |
| Source resolution | `explicitSource === "system"` branches |
| Status maps | Consumes `queue` and `system` maps from runtime config |

#### Tests
| Area | Files |
|------|-------|
| Queue layout fixtures | [queue_rows.js](../../tests/task_dashboard/__fixtures__/queue_rows.js) — `createsystemRow()` |
| Submit runtime tests | [test_submit_runtime.js](../../tests/task_dashboard/test_submit_runtime.js) — system validation, priority, UI state |
| Queue layout tests | [test_queue_layouts.js](../../tests/task_dashboard/test_queue_layouts.js) — system row rendering |
| View model tests | [test_task_dashboard_view_model.py](../../tests/unit/api/routers/test_task_dashboard_view_model.py) |
| system removal coverage | [test_doc_req_coverage.py](../../tests/unit/orchestrator_removal/test_doc_req_coverage.py) |

#### Docs
| Document | Issue |
|----------|-------|
| [SourceOfTruthAndProjectionModel.md](../Temporal/SourceOfTruthAndProjectionModel.md) | Describes mixed-source as "migration stance", projection as "temporary implementation posture" |
| [TaskExecutionCompatibilityModel.md](../Temporal/TaskExecutionCompatibilityModel.md) | Lists `queue`, `system`, `temporal` as execution sources; multi-source pagination rules |
| [VisibilityAndUiQueryModel.md](../Temporal/VisibilityAndUiQueryModel.md) | References mixed-source pages, `queue`/`system` dashboard sources |

---

## Migration Phases

### Phase 1 — Queue Submission Path → Temporal *(prerequisite)*

**Objective:** Ensure every action that currently routes through `/api/queue/jobs` can be performed through the Temporal execution path instead.

- [x] **1.1** Audit queue-only features: attachments, live sessions, operator messages, task control, events/SSE, skills list
- [x] **1.2** Map each queue feature to its Temporal equivalent or mark as deferred
- [x] **1.3** Ensure Temporal submit supports all current submit form fields: runtime, model, effort, repository, publish mode, attachments
- [x] **1.4** Redirect manifest submission (`/api/queue/jobs?type=manifest`) to `MoonMind.ManifestIngest` Temporal workflow
- [x] **1.5** Confirm recurring tasks (`/api/recurring-tasks`) already use Temporal Schedules (check if still queue-backed)
- [x] **1.6** Verify step templates work against Temporal execution path

> **Gate:** No user-facing action requires the queue path. Queue router can be deprecated without feature loss.

---

### Phase 2 — Collapse Dashboard to Single Source

**Objective:** Remove `queue` and `system` as dashboard execution sources. All task list/detail goes through `temporal`.

- [x] **2.1** Remove `sources.queue` from `build_runtime_config()` in `task_dashboard_view_model.py`
- [x] **2.2** Remove `sources.manifests` queue-backed endpoint block (manifests should use Temporal source)
- [x] **2.3** Remove `queue` and `system` from `_STATUS_MAPS` — only `proposals` and `temporal` remain
- [x] **2.4** Simplify `normalize_status()` — single mapping for Temporal states
- [x] **2.5** In `dashboard.js`: remove system route matching, form validation stubs, priority normalization, UI state branches
- [x] **2.6** In `dashboard.js`: remove queue source fetcher/renderer code; point all task list/detail at Temporal endpoints
- [x] **2.7** Remove `source` filter from compatibility APIs (always `temporal`) or deprecate the parameter
- [x] **2.8** Update test fixtures: remove `createsystemRow()`, `createQueueRow()`, update to Temporal-only rows
- [x] **2.9** Update submit runtime tests to remove system validation/priority tests
- [x] **2.10** Update view model tests for single-source config

> **Gate:** Dashboard renders from Temporal source only. No `queue`/`system` branching in frontend or view model.

---

### Phase 3 — Remove Queue Backend Code

**Objective:** Delete the legacy queue execution substrate code.

- [ ] **3.1** Remove `api_service/api/routers/agent_queue.py` and its inclusion in the API router setup
- [ ] **3.2** Delete `moonmind/workflows/agent_queue/` module (~250 KB of service, repository, model, contract code)
- [ ] **3.3** Remove queue-related DB models and generate Alembic migration to drop queue tables
- [ ] **3.4** Remove queue environment variables from settings (`MOONMIND_QUEUE`, `defaultQueue`, `queueEnv`)
- [x] **3.5** Remove `moonmind/workflows/system/` directory (empty except `__pycache__`)
- [ ] **3.6** Remove `tests/unit/orchestrator_removal/` directory (removal is now complete)
- [ ] **3.7** Remove queue-related integration tests and contract tests
- [ ] **3.8** Clean up `moonmind/workflows/__init__.py` for queue/system exports

> **Gate:** `agent_queue` and `system` modules are deleted. No queue tables in DB schema.

---

### Phase 4 — Eliminate Compatibility Layer Complexity

**Objective:** Simplify the compatibility adapters now that only one source exists.

- [ ] **4.1** Simplify `TaskCompatibilityService` — remove multi-source routing, source resolution logic
- [ ] **4.2** Simplify or merge `task_compatibility.py` into `executions.py` — single source means no bridging needed
- [ ] **4.3** Remove `source_mapping.py` / `TaskResolutionAmbiguousError` — ambiguity is impossible with one source
- [ ] **4.4** Simplify `TemporalExecutionService` — remove "staging" caveats, make it the direct service
- [ ] **4.5** Consider merging `/api/tasks/*` and `/api/executions/*` into a single API surface
- [ ] **4.6** Remove `temporalCompatibility` config block from view model (compatibility is just the default now)
- [ ] **4.7** Remove `taskSourceResolver` endpoint — no source resolution needed

> **Gate:** No multi-source routing code. API surface is clean.

---

### Phase 5 — Update Documentation

**Objective:** Remove transitional language from architecture docs.

- [ ] **5.1** Update `SourceOfTruthAndProjectionModel.md`: remove "migration stance", "staging", "mixed-source" sections; promote steady-state as the only contract
- [ ] **5.2** Update `TaskExecutionCompatibilityModel.md`: remove `queue`/`system` source definitions, multi-source pagination rules; simplify to Temporal-only or archive the doc
- [ ] **5.3** Update `VisibilityAndUiQueryModel.md`: remove mixed-source references, retire multi-source pagination section
- [x] **5.4** Delete `docs/tmp/OrchestratorRemovalPlan.md` — fully superseded *(removed)*
- [ ] **5.5** Update `docs/MoonMindArchitecture.md` if any queue/system references remain
- [ ] **5.6** Update Roadmap: close H.1 (system removal) and mark this plan's items as done
- [ ] **5.7** Delete this document once complete

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Existing queue-backed tasks in production DB | Migration must handle or archive in-flight queue jobs. Provide a read-only view or one-time export before dropping tables. |
| Feature gap in Temporal path | Phase 1 gate: complete feature audit before removing anything |
| Queue SSE events not replicated in Temporal | Temporal already has SSE via the execution event system; verify parity |
| Attachment upload path | Verify artifact system handles all attachment use cases |
| Recurring tasks still queue-backed | Must verify — if so, migrate to Temporal Schedules first |

## Relationship to Roadmap

This plan **subsumes and extends** Housekeeping item **H.1** (Complete system removal). It goes further by also removing the queue substrate and collapsing the compatibility layer.

Successful completion delivers:
- ✅ One task model (Temporal workflow execution)
- ✅ One execution model (Temporal activities + workers)
- ✅ One observability model (Temporal Visibility + projection cache)
- ✅ One action model (Temporal start/update/signal/cancel)
- ✅ Simpler API surface
- ✅ Simpler dashboard code
- ✅ Simpler docs
