# Single Execution Substrate Migration Plan

> **Goal:** Finish the move to Temporal-backed execution as the **only** real substrate. Remove legacy queue and system paths so MoonMind has one task model, one execution model, one observability model, and one action model.
>
> **Last updated:** 2026-03-24

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
- legacy queue router and `moonmind/workflows/agent_queue/` removed; queue backend tables dropped via migration `b92f4891f27c`
- system DB tables dropped via migration `c1d2e3f4a5b6`
- External Runs tab removed from dashboard
- system submissions rejected with error in `dashboard.js`

### What Still References Legacy Sources ⚠️

#### Python Backend
| Component | File | Legacy Reference |
|-----------|------|-----------------|
| View model | [task_dashboard_view_model.py](../../api_service/api/routers/task_dashboard_view_model.py) | Residual **system** block keys (`defaultQueue`, `queueEnv`, `taskSourceResolver`); submit defaults from `moonmind.workflows.tasks.runtime_defaults` |
| Task compatibility router | [task_compatibility.py](../../api_service/api/routers/task_compatibility.py) | Deprecated `source` / `source_hint` query params; handlers pass `temporal` only — Phase 4 will remove the adapter surface |
| Task routing | [routing.py](../../moonmind/workflows/tasks/routing.py) | Any remaining non-Temporal routing names |
| Automation | [automation/](../../moonmind/workflows/automation/) | Naming/orchestration that predates single substrate |
| Settings | [settings.py](../../moonmind/config/settings.py) | `MOONMIND_QUEUE` alias still used for **Codex worker queue** (`codex_queue`), not the removed agent queue API |

#### Frontend (dashboard.js)
| Area | Legacy Reference |
|------|-----------------|
| Worker runtime capabilities | `queueSourceConfig` + default `/api/queue/workers/runtime-capabilities` when `sources.queue` absent — should move to Temporal/worker-auth config (Phase 3+) |
| Copy / labels | `defaultQueueName` and “Unified queue” strings — cosmetic naming cleanup |
| Task resolution | Optional calls to `taskSourceResolver` / `taskResolution` endpoints until Phase 4 removes them |

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

**Progress:** Phases **1**, **2**, **3**, and **4** are **complete**. Phase **5** is **not started**.

### Phase 1 — Queue Submission Path → Temporal *(prerequisite)* — **COMPLETE**

**Objective:** Ensure every action that currently routes through `/api/queue/jobs` can be performed through the Temporal execution path instead.

**Status:** Finished. Gate met: no user-facing action requires the removed queue path.

- [x] **1.1** Audit queue-only features: attachments, live sessions, operator messages, task control, events/SSE, skills list
- [x] **1.2** Map each queue feature to its Temporal equivalent or mark as deferred
- [x] **1.3** Ensure Temporal submit supports all current submit form fields: runtime, model, effort, repository, publish mode, attachments
- [x] **1.4** Redirect manifest submission (`/api/queue/jobs?type=manifest`) to `MoonMind.ManifestIngest` Temporal workflow
- [x] **1.5** Confirm recurring tasks (`/api/recurring-tasks`) already use Temporal Schedules (check if still queue-backed)
- [x] **1.6** Verify step templates work against Temporal execution path

---

### Phase 2 — Collapse Dashboard to Single Source — **COMPLETE**

**Objective:** Remove `queue` and `system` as dashboard execution sources. All task list/detail goes through `temporal`.

**Status:** Finished. Dashboard execution views use Temporal; residual strings and worker-capability URL fallbacks are Phase 3+ cleanup.

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

---

### Phase 3 — Remove Queue Backend Code — **COMPLETE**

**Objective:** Delete the legacy queue execution substrate code and scrub remaining queue-shaped config, tests, and UI fallbacks.

**Status:** Finished. All backend code, configuration aliases, UI fallbacks, and related tests have been removed or updated.

- [x] **3.1** Remove `api_service/api/routers/agent_queue.py` and its inclusion in the API router setup *(removed)*
- [x] **3.2** Delete `moonmind/workflows/agent_queue/` module (~250 KB of service, repository, model, contract code) *(removed; shared helpers live under `moonmind/workflows/tasks/` where noted in file headers)*
- [x] **3.3** Remove queue-related DB models and generate Alembic migration to drop queue tables *(see `b92f4891f27c_remove_legacy_queue_backend_tables.py`)*
- [x] **3.4** Remove or rename queue-shaped dashboard settings: `defaultQueue`, `queueEnv` in view model; align worker queue naming vs Codex `MOONMIND_QUEUE` in settings; update dashboard copy and runtime-capabilities URL config (no default `/api/queue/...` path)
- [x] **3.5** Remove `moonmind/workflows/system/` directory (empty except `__pycache__`)
- [x] **3.6** Remove `tests/unit/orchestrator_removal/` directory (e.g. migrate or drop `test_doc_req_coverage.py` once redundant)
- [x] **3.7** Remove or rewrite queue-related tests: skipped `test_agent_queue_artifacts.py`, JS fixtures still referencing `/api/queue/jobs`, e2e mocks for queue routes
- [x] **3.8** Clean up `moonmind/workflows/__init__.py` for queue/system exports *(no `get_agent_queue_service`; Temporal factories only)*

> **Gate:** `agent_queue` and `system` workflow modules are deleted; queue tables removed from schema. Open items **3.4–3.7** finish config/test/e2e cleanup so nothing queue-shaped remains in the operator surface.

---

### Phase 4 — Eliminate Compatibility Layer Complexity — **COMPLETE**

**Objective:** Simplify the compatibility adapters now that only one source exists.

**Status:** Finished. Adapter layer removed, dashboard config simplified.

- [x] **4.1** Simplify `TaskCompatibilityService` — remove multi-source routing, source resolution logic
- [x] **4.2** Simplify or merge `task_compatibility.py` into `executions.py` — single source means no bridging needed
- [x] **4.3** Remove `source_mapping.py` / `TaskResolutionAmbiguousError` — ambiguity is impossible with one source
- [x] **4.4** Simplify `TemporalExecutionService` — remove "staging" caveats, make it the direct service
- [x] **4.5** Consider merging `/api/tasks/*` and `/api/executions/*` into a single API surface
- [x] **4.6** Remove `temporalCompatibility` config block from view model (compatibility is just the default now)
- [x] **4.7** Remove `taskSourceResolver` endpoint — no source resolution needed

> **Gate:** No multi-source routing code. API surface is clean.

---

### Phase 5 — Update Documentation

**Objective:** Remove transitional language from architecture docs.

- [ ] **5.1** Update `SourceOfTruthAndProjectionModel.md`: remove "migration stance", "staging", "mixed-source" sections; promote steady-state as the only contract
- [ ] **5.2** Update `TaskExecutionCompatibilityModel.md`: remove `queue`/`system` source definitions, multi-source pagination rules; simplify to Temporal-only or archive the doc
- [ ] **5.3** Update `VisibilityAndUiQueryModel.md`: remove mixed-source references, retire multi-source pagination section
- [x] **5.4** Delete `docs/tmp/OrchestratorRemovalPlan.md` — fully superseded *(removed)*
- [ ] **5.5** Update `docs/MoonMindArchitecture.md` if any queue/system references remain
- [ ] **5.6** Update remaining Temporal architecture docs (`TemporalArchitecture.md`, `ActivityCatalogAndWorkerTopology.md`, `WorkflowTypeCatalogAndLifecycle.md`, `RoutingPolicy.md`) to remove transitional queue/system language, purge old feature flags, and promote steady-state Temporal vocabulary *(formerly tracked in 020-026 tracker files)*
- [ ] **5.7** Update Roadmap: close H.1 (system removal) and mark this plan's items as done
- [ ] **5.8** Delete this document once complete

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
