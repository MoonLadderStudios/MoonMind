# Implementation Plan: Temporal Compose Foundation

**Branch**: `044-temporal-compose-foundation` | **Date**: 2026-03-05 | **Spec**: `specs/044-temporal-compose-foundation/spec.md`  
**Input**: Feature specification from `/specs/044-temporal-compose-foundation/spec.md`

## Summary

Deliver a runtime-grade Temporal foundation that is Docker Compose managed, PostgreSQL-backed for persistence plus SQL visibility, and aligned to a Temporal-first/Celery-free execution model. The plan extends existing compose/bootstrap scaffolding into full runtime behavior: Temporal-native lifecycle APIs (start/update/signal/cancel/list/describe), namespace/retention automation, schedule migration, worker queue/versioning policy enforcement, upgrade rehearsal gates, and automated validation coverage with explicit runtime-mode scope.

## Technical Context

**Language/Version**: Python 3.11 backend/services, shell scripts for runtime bootstrap, Docker Compose YAML  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, `temporalio` Python SDK (planned), Temporal server/admin tools images, existing artifact storage interfaces  
**Storage**: PostgreSQL for MoonMind app state + Temporal persistence + SQL visibility; artifact store for large payload/log references  
**Testing**: `./tools/test_unit.sh` (unit/contract), Temporal compose validation scripts, targeted integration checks via Docker Compose runtime  
**Target Platform**: Linux Docker Compose MoonMind deployment with private-network Temporal gRPC (`temporal:7233`)  
**Project Type**: Multi-service backend + worker runtime + infrastructure compose definitions  
**Performance Goals**: Foundation startup + readiness checks under 15 minutes (SC-001); list/filter pagination sourced from Temporal Visibility with deterministic page token behavior  
**Constraints**: Temporal-first semantics with no competing workflow engine behavior; task queues routing-only; workers poll task queues directly without Temporal Worker Deployment routing; shard-count decision gate enforced; runtime-vs-docs mode remains runtime-authoritative (implementation + tests required)
**Scale/Scope**: Compose foundation hardening, execution lifecycle APIs, schedule orchestration, manifest failure policy semantics, observability/upgrade readiness guardrails, and end-to-end validation traceability for DOC-REQ-001 through DOC-REQ-015

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. The Bittersweet Lesson**: PASS. Plan isolates volatile runtime migration surfaces behind explicit Temporal contracts so scaffolding can be replaced without contract drift.
- **II. One-Click Deployment with Smart Defaults**: PASS. Compose-driven Temporal foundation with env defaults (`TEMPORAL_*`) preserves fresh-clone operator path.
- **III. Powerful Runtime Configurability**: PASS. Namespace, retention, shard count, scheduling behavior, and lifecycle controls stay env/request/config driven.
- **IV. Modular and Extensible Architecture**: PASS. Introduces Temporal client/adapter modules and workflow APIs without entangling unrelated feature paths.
- **V. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Self-hosted Temporal + portable API/artifact formats avoid proprietary runtime dependency.
- **VI. Self-Healing by Default**: PASS. Idempotent namespace reconciliation, Temporal retry semantics, and explicit failure-policy controls are first-class.
- **VII. Facilitate Continuous Improvement**: PASS. Observability and upgrade rehearsal artifacts provide structured operational feedback loops.
- **VIII. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` coverage is fully traced in planning artifacts and remains a release gate.
- **IX. Skills Are First-Class and Easy to Add**: PASS. Skill execution remains activity-routed and runtime-neutral under Temporal task-queue routing.

### Post-Design Re-Check

- PASS. Phase 1 artifacts preserve contract-first boundaries for foundation, lifecycle API, and scheduling.
- PASS. Runtime-vs-docs alignment remains explicit: runtime mode requires production code plus validation tests.
- PASS. No constitution violations require Complexity Tracking exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/044-temporal-compose-foundation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-executions.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docker-compose.yaml
.env-template
services/temporal/
├── dynamicconfig/development-sql.yaml
└── scripts/
    ├── bootstrap-namespace.sh
    └── rehearse-visibility-schema-upgrade.sh        # planned

moonmind/
├── config/settings.py
├── schemas/
│   ├── workflow_models.py
│   └── temporal_models.py                            # planned
└── workflows/
    ├── temporal/
    │   ├── client.py                                 # planned
    │   ├── service.py                                # planned
    │   ├── schedules.py                              # planned
    │   └── workers.py                                # planned
    ├── recurring_tasks/
    │   └── scheduler.py                              # planned migration/removal
    └── agentkit_celery/                               # planned de-scope of runtime execution ownership

api_service/
├── api/routers/
│   ├── executions.py                                 # planned
│   ├── recurring_tasks.py                            # planned schedule alignment
│   └── workflows.py                                  # planned compatibility/deprecation handling
├── db/
│   └── models.py                                     # planned Temporal execution projections/search attrs
└── migrations/versions/                              # planned schema additions/updates

tests/
├── contract/
│   └── test_temporal_execution_api.py                # planned
├── integration/
│   └── temporal/
│       ├── test_compose_foundation.py               # planned
│       ├── test_namespace_retention.py              # planned
│       └── test_visibility_and_lifecycle.py         # planned
└── unit/
    ├── workflows/temporal/                          # planned
    ├── api/routers/test_executions.py               # planned
    └── services/test_temporal_foundation.py         # planned
```

**Structure Decision**: Keep existing repository layout and add a dedicated `moonmind/workflows/temporal/` runtime module with an `api_service/api/routers/executions.py` API surface. Infrastructure automation remains under `services/temporal/` and compose/env roots.

## Phase 0 – Research Summary

Research outcomes in `specs/044-temporal-compose-foundation/research.md` establish:

1. Keep self-hosted Docker Compose Temporal topology as the baseline and harden it with explicit visibility upgrade rehearsal automation.
2. Introduce a dedicated Temporal runtime service layer (client + schedule manager + namespace/health utilities) rather than embedding SDK calls in routers.
3. Make Temporal Visibility the source of truth for execution listing/count/filter behavior and treat local DB as supplemental metadata only.
4. Enforce queue semantics as routing-only with explicit task-queue taxonomy and direct worker polling.
5. Replace recurring scheduler ownership with Temporal Schedules and make manifest failure policies explicit runtime parameters.
6. Keep runtime-vs-docs orchestration mode aligned to runtime implementation intent.

## Phase 1 – Design Outputs

- **Data Model**: `data-model.md` defines deployment profile, retention policy, execution lifecycle contract, visibility model, schedule definition, and upgrade readiness entities.
- **API Contract**: `contracts/temporal-executions.openapi.yaml` defines foundation + lifecycle endpoints aligned to Temporal execution semantics.
- **Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-001` through `DOC-REQ-015` to FRs, implementation surfaces, implementation task coverage, and validation task coverage.
- **Execution Guide**: `quickstart.md` provides runtime-mode implementation and validation flow for compose foundation and API lifecycle behaviors.

## Implementation Strategy

### 1. Compose foundation and namespace automation

- Keep `temporal-db`, `temporal`, `temporal-namespace-init`, and optional tool/UI profiles as canonical runtime.
- Add explicit upgrade rehearsal script for SQL visibility schema compatibility (`services/temporal/scripts/rehearse-visibility-schema-upgrade.sh`).
- Ensure private-network-only exposure and no public gRPC publication.
- Keep shard-count gate explicit (`TEMPORAL_NUM_HISTORY_SHARDS`) with rollout acknowledgment checks.

### 2. Temporal runtime settings and client layer

- Add Temporal settings model in `moonmind/config/settings.py` and wire `TEMPORAL_*` variables with safe defaults.
- Implement Temporal client/service abstractions under `moonmind/workflows/temporal/` for:
  - workflow start/update/signal/cancel/describe/list/count,
  - namespace retention reconciliation,
  - schedule upsert/pause/resume/trigger.
- Keep artifact payload references in workflow/activity I/O to avoid history bloat.

### 3. Lifecycle API surface and visibility-backed listing

- Add canonical runtime API in `api_service/api/routers/executions.py`:
  - `POST /api/executions`
  - `POST /api/executions/{workflowId}/update`
  - `POST /api/executions/{workflowId}/signal`
  - `POST /api/executions/{workflowId}/cancel`
  - `GET /api/executions`
  - `GET /api/executions/{workflowId}`
- Use Temporal Visibility page tokens and search-attribute filters; avoid merged pager semantics.
- Map compatible legacy workflow routes to Temporal-backed execution views with explicit deprecation signaling.

### 4. Scheduling and manifest failure-policy behavior

- Re-home recurring trigger ownership from custom scheduler polling to Temporal Schedules.
- Add explicit failure-policy contract handling for manifest ingestion (`fail_fast`, `continue_and_report`, `best_effort`) in Temporal workflow inputs.
- Support external monitoring interactions through signals/callback events with timer-based polling fallback.

### 5. Worker topology and routing

- Define stable routing queues (`mm.workflow`, `mm.activity.*`) and enforce queue routing-only semantics.
- Start workers without Temporal Worker Deployment routing or current-version setup.
- Add observability hooks for no-poller conditions, retry storms, and visibility query failures.

### 6. Upgrade readiness and operational guardrails

- Implement upgrade readiness record/checkpoints before rollout approval:
  - visibility schema rehearsal result,
  - server/tool/schema version compatibility snapshot,
  - shard decision acknowledgment.
- Ensure retention governance is explicit, idempotent, and storage-cap driven (`TEMPORAL_RETENTION_MAX_STORAGE_GB=100` default).

### 7. Validation strategy

- Contract tests for execution lifecycle and visibility pagination/filter/token semantics.
- Integration tests for compose startup, namespace reconciliation idempotency, schedule behavior, and retention/visibility validation.
- Unit tests for settings parsing, Temporal client adapters, failure-policy branching, and schedule service behavior.
- Execute core acceptance through `./tools/test_unit.sh` plus targeted compose-based validation commands in `quickstart.md`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion requires production runtime code changes plus automated validation tests; docs/spec-only output is non-compliant for this feature.
- Docs-mode guidance remains aligned by preserving the same `DOC-REQ-*` traceability, lifecycle contract surface, and validation gate definitions.

## Remediation Gates (Prompt B)

- All `DOC-REQ-*` rows must map to at least one FR, planned implementation surface, and planned validation strategy.
- Temporal Visibility must remain the source of truth for list/filter/pagination behavior.
- Task queue semantics must remain routing-only and must not introduce user-visible queue ordering claims.
- Runtime mode requires both implementation and validation tasks in `tasks.md`; docs-only task sets are invalid.
- Upgrade rollout must be blocked when visibility schema rehearsal is absent or failed.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T001-T015`, `T019-T021`, `T027-T033`, `T037-T040`) and validation tasks (`T016-T018`, `T022-T026`, `T034-T036`, `T042-T043`) in `tasks.md`.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for every source requirement (`DOC-REQ-001` through `DOC-REQ-015`) in `contracts/requirements-traceability.md`.
- Cross-artifact determinism is preserved: spec intent (runtime-authoritative delivery), plan constraints, and task execution coverage align without contradictory scope language.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls and a `DOC-REQ Coverage Matrix` in `tasks.md` to make runtime/validation gating auditable before implementation.
- Reinforced scope-validation execution requirements in task quality gates so runtime checks remain explicit during implementation handoff.

### Residual risks

- Celery-to-Temporal migration touches multiple modules; hidden legacy execution coupling can surface during implementation and integration testing.
- Compose and schema-rehearsal checks rely on target-environment parity; divergence between local and deployment infra may require additional operational hardening.

## Risks & Mitigations

- **Migration risk (Celery to Temporal runtime semantics)**: keep adapters explicit and add compatibility/deprecation tests for legacy workflow endpoints.
- **Operational drift in visibility schema upgrades**: enforce rehearsal script and rollout gate checks in CI/ops runbook.
- **Queue semantics regression in UI/API**: encode routing-only language into contracts and regression tests.
- **History size growth from large payloads**: enforce artifact-reference contracts and add serialization guard tests.
- **Schedule behavior drift during migration**: validate schedule upsert/trigger/cancel paths against Temporal APIs in integration tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
