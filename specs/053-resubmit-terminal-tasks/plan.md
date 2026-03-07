# Implementation Plan: Resubmit Terminal Tasks

**Branch**: `043-resubmit-terminal-tasks` | **Date**: 2026-03-01 | **Spec**: `specs/043-resubmit-terminal-tasks/spec.md`  
**Input**: Feature specification from `/specs/043-resubmit-terminal-tasks/spec.md`

## Summary

Extend existing queue-task edit prefill behavior so terminal task jobs (`failed`/`cancelled`) can be resubmitted as a new job through a first-class backend endpoint, while preserving source history and adding explicit source-to-new audit linkage. The selected orchestration mode is runtime (not docs-only), so completion requires production runtime code changes plus validation tests.

## Technical Context

**Language/Version**: Python 3.11 backend services + vanilla JavaScript dashboard  
**Primary Dependencies**: FastAPI, Pydantic v2 schemas, SQLAlchemy async repository/service stack, existing queue payload normalization (`normalize_task_job_payload`), dashboard runtime config/view-model plumbing  
**Storage**: PostgreSQL `agent_jobs` + `agent_job_events` + existing artifact metadata tables (reuse existing create/event paths; no new schema required for v1)  
**Testing**: `./tools/test_unit.sh` (Python unit tests + dashboard JS tests)  
**Target Platform**: Linux/Docker Compose MoonMind API + queue worker + `/tasks/*` dashboard routes  
**Project Type**: Multi-service backend + static frontend dashboard  
**Performance Goals**: Keep resubmit path to one source-read + one create transaction; keep dashboard prefill flow unchanged except mode resolution; avoid extra polling or duplicate fetch loops  
**Constraints**: Resubmit eligibility limited to `type=task` with `status in {failed,cancelled}`; queued/running stay non-resubmittable; source job remains immutable; v1 attachments are not copied; edit behavior for queued never-started jobs must remain unchanged; keep runtime-vs-docs behavior aligned with runtime implementation intent  
**Scale/Scope**: Applies to all user-owned queue task detail flows that support prefill/create today, including API/service/UI/docs/tests coverage for eligibility, authorization, and audit traceability

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Deployment with Smart Defaults**: PASS. Change is additive in existing API/service/dashboard modules and introduces no new required infrastructure or secrets.
- **II. Powerful Runtime Configurability**: PASS. Dashboard endpoint templates remain runtime-injected (`sources.queue.resubmit`) and no hardcoded environment behavior is added.
- **III. Modular and Extensible Architecture**: PASS. Work stays inside existing queue module boundaries (schema/router/service/dashboard) without cross-cutting rewrites.
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Resubmit contract is queue-runtime agnostic and reuses portable job payload formats.
- **V. Self-Healing by Default**: PASS. Source eligibility checks and explicit conflict responses keep retry behavior deterministic under state races.
- **VI. Facilitate Continuous Improvement**: PASS. Source/new linkage events increase traceability for operator analysis and follow-up actions.
- **VII. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` coverage is explicit via requirements traceability and runtime-scope gates.
- **VIII. Skills Are First-Class and Easy to Add**: PASS. Resubmit reuses canonical task payload/skill contract normalization and does not bypass skill interfaces.

### Post-Design Re-Check

- PASS. Phase 1 artifacts preserve additive module boundaries, explicit API/UI contracts, and runtime-mode alignment.
- PASS. No constitution violations require Complexity Tracking exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/043-resubmit-terminal-tasks/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── task-resubmit.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/agent_queue_models.py
└── workflows/agent_queue/
    ├── models.py
    └── service.py

api_service/
├── api/routers/
│   ├── agent_queue.py
│   └── task_dashboard_view_model.py
└── static/task_dashboard/dashboard.js

docs/
├── TaskEditingSystem.md
├── TaskQueueSystem.md
└── TaskUiArchitecture.md

tests/
├── unit/workflows/agent_queue/test_service_resubmit.py
├── unit/api/routers/test_agent_queue.py
├── unit/api/routers/test_task_dashboard_view_model.py
└── task_dashboard/test_submit_runtime.js
```

**Structure Decision**: Reuse existing queue service/router/dashboard architecture and the current `/tasks/queue/new?editJobId=<jobId>` prefill entrypoint; add resubmit behavior as a mode extension rather than a new feature surface.

## Phase 0 – Research Summary

Research outcomes in `specs/043-resubmit-terminal-tasks/research.md` establish:

1. First-class `POST /api/queue/jobs/{jobId}/resubmit` is preferred over UI-only create replay for audit and policy consistency.
2. Eligibility and mode selection are server-authoritative: queued+never-started remains edit; failed/cancelled task becomes resubmit.
3. Service path must enforce owner authorization parity, normalization parity, and deterministic conflicts.
4. v1 attachment policy remains no-copy/no-mutation for resubmit requests.
5. Runtime-vs-docs orchestration mode stays runtime-only: implementation + tests are mandatory.

## Phase 1 – Design Outputs

- **Data Model**: `data-model.md` defines source eligibility snapshot, resubmit request envelope, new-job lineage model, audit event payloads, and dashboard mode state.
- **API Contract**: `contracts/task-resubmit.openapi.yaml` defines terminal task resubmit endpoint request/response/error semantics and detail-prefill dependency.
- **Traceability**: `contracts/requirements-traceability.md` maps all `DOC-REQ-*` items to FRs, implementation surfaces, and validation strategies.
- **Execution Guide**: `quickstart.md` defines runtime-mode implementation flow and validation commands.

## Implementation Strategy

### 1. Queue API and schema contract

- Add `ResubmitJobRequest` request model aligned with create/update envelope (`type`, `priority`, `maxAttempts`, `affinityKey`, `payload`) plus optional `note`.
- Expose `POST /api/queue/jobs/{jobId}/resubmit` returning `JobModel` with `201`.
- Keep router error mapping aligned with queue semantics (`404`, `403`, `409`, `422`, runtime-gate `400`).

### 2. Service-level resubmit transaction

- Load source job and enforce owner authorization parity with queued edit semantics.
- Enforce eligibility invariants (`type="task"` and `status in {failed,cancelled}`), reject queued/running/wrong-type sources.
- Reuse task payload normalization/runtime-gate checks from create/update.
- Create new queued job record through existing create path (source untouched), append `Job resubmitted` on source and `Job resubmitted from` on new job, then commit.

### 3. Dashboard mode extension (create/edit/resubmit)

- Reuse prefill query parameter and resolve mode from fetched source job (`edit` vs `resubmit`) to avoid route proliferation.
- Show `Resubmit` action on eligible detail pages and route to existing prefill create page.
- In resubmit mode, keep form prefill behavior, switch primary CTA label to `Resubmit`, use resubmit endpoint on submit, return cancel navigation to source detail, and redirect success to new detail with `resubmittedFrom` notice.
- Display explicit v1 attachment guidance: source attachments are not copied.

### 4. Documentation and runtime config alignment

- Ensure dashboard runtime config includes `sources.queue.resubmit`.
- Update task editing/queue/UI architecture docs to reflect eligibility split, endpoint contract, and no-attachment-copy v1 behavior.

### 5. Validation strategy

- Service tests: success path + ineligible state/type + non-owner + audit linkage assertions.
- Router tests: status/error mapping and superuser authorization flag propagation.
- Dashboard tests: query parsing/mode inference, resubmittable gating, CTA labeling, endpoint routing, redirect notice handling.
- Run acceptance suite through `./tools/test_unit.sh`.

### 6. Runtime-vs-docs mode alignment gate

- This feature is runtime-scoped (`Implementation Intent: Runtime implementation` in `spec.md`).
- Planning and execution must include production code/test work; docs/spec-only completion fails FR-018 and SC-006.
- Runtime scope checks should remain part of implementation validation (`validate-implementation-scope.sh --mode runtime`).

## Remediation Gates (Prompt B)

- `DOC-REQ-*` coverage must remain complete: each requirement maps to at least one FR, implementation surface, and validation strategy.
- Runtime mode requires both implementation and validation tasks in `tasks.md`; docs-only task sets are invalid.
- Existing queued-edit behavior for queued+never-started jobs must remain unchanged while resubmit covers only terminal task jobs.
- Attachment no-copy v1 policy must be explicit in UI and docs to avoid hidden behavior drift.

## Risks & Mitigations

- **State-race regression between prefill and submit**: enforce source-state check at submit time and map ineligible transitions to conflict responses.
- **Authorization drift between edit and resubmit**: reuse identical owner semantics and add targeted non-owner tests.
- **Audit lineage gaps**: require source/new event assertions in service tests and include route-level validation.
- **UI contract drift across modes**: centralize mode resolution and endpoint selection logic with dashboard test coverage.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
