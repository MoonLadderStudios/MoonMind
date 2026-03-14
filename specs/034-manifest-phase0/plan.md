# Implementation Plan: Manifest Phase 0 Rebaseline

**Branch**: `031-manifest-phase0` | **Date**: 2026-03-02 | **Spec**: `/specs/031-manifest-phase0/spec.md`
**Input**: Feature specification from `/specs/031-manifest-phase0/spec.md`

## Summary

Rebaseline Manifest Phase 0 artifacts to the current MoonMind runtime contract, with runtime implementation mode as the governing execution path. Planning output is anchored to the as-built queue and registry behavior (`manifest_contract.py`, queue service/repository, manifest router/service, and serializer models), and explicitly rejects docs-only closure: any discovered requirement/runtime mismatch must be resolved through production code changes and validated with `./tools/test_unit.sh`.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, SQLAlchemy 2 (async), Pydantic 2, PyYAML, Alembic, Celery
**Storage**: PostgreSQL (`agent_queue*` tables + `manifest` registry table with JSONB state fields)
**Testing**: `./tools/test_unit.sh` (required), runtime scope gates via `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
**Target Platform**: Linux containers via Docker Compose (`api`, `celery-worker`, `rabbitmq`, `postgres`)
**Project Type**: Monorepo backend services (API + queue worker + orchestration)
**Performance Goals**: Preserve existing queue API and claim-loop behavior; keep normalization deterministic and bounded by payload size
**Constraints**: Runtime implementation mode only (no docs-only acceptance), fail-fast validation for unsupported Phase 0 inputs, token-safe persistence/serialization, compatibility-policy compliance (no hidden semantic/billing transforms)
**Scale/Scope**: Manifest Phase 0 control-plane contract (queue + registry + routing metadata + tests), not full data-plane ingestion execution

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. One-Click Deployment with Smart Defaults | PASS | Uses existing Docker Compose runtime; no additional secret requirements introduced by planning scope. |
| II. Powerful Runtime Configurability | PASS | Manifest source-kind gating and base capability labels are runtime-configurable (`allow_manifest_path_source`, `manifest_required_capabilities`). |
| III. Modular and Extensible Architecture | PASS | Manifest validation stays in dedicated contract module; queue service/router consume explicit interfaces. |
| IV. Avoid Exclusive Proprietary Vendor Lock-In | PASS | Capability derivation supports multiple providers (`openai`, `google`, `ollama`) through normalized labels. |
| V. Self-Healing by Default | PASS | Queue lifecycle/retry/claim semantics remain unchanged; manifest contract keeps deterministic normalization for safe retries. |
| VI. Facilitate Continuous Improvement | PASS | Plan includes explicit traceability updates plus regression-test requirements tied to runtime behavior. |
| VII. Spec-Driven Development Is the Source of Truth | PASS | This rebaseline aligns plan/research/data-model/contracts/quickstart with current runtime and spec requirements. |
| VIII. Skills Are First-Class and Easy to Add | PASS | Work remains adapter-neutral and skill-driven (`agentkit-plan`) without runtime-specific contract divergence. |

## Project Structure

### Documentation (this feature)

```text
specs/031-manifest-phase0/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── manifest-task-system-phase0.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── agent_queue.py
│   └── manifests.py
├── api/schemas.py
├── db/
│   ├── models.py
│   └── migrations/versions/
└── services/manifests_service.py

moonmind/
├── config/settings.py
├── schemas/agent_queue_models.py
└── workflows/agent_queue/
    ├── job_types.py
    ├── manifest_contract.py
    ├── repositories.py
    └── service.py

tests/unit/
├── api/routers/
│   ├── test_agent_queue.py
│   └── test_manifests.py
├── services/test_manifests_service.py
└── workflows/agent_queue/
    ├── test_manifest_contract.py
    └── test_repositories.py
```

**Structure Decision**: Use the current monorepo backend structure and update only manifest queue/registry contracts, supporting schemas, and targeted unit tests. No new top-level modules are required for this rebaseline step.

## Phase Plan

### Phase 0: Research and Contract Reconciliation

- Confirm as-built manifest normalization, source-kind gates, secret-leak detection, capability derivation, and serializer sanitization.
- Resolve all technical unknowns in `research.md` with decisions, rationale, and rejected alternatives.

### Phase 1: Design Artifacts

- Update `data-model.md` to separate persisted queue payload from sanitized API payload semantics.
- Update OpenAPI and traceability contracts under `contracts/`.
- Refresh `quickstart.md` to reflect current API behavior and error semantics.

### Phase 2: Execution Handoff

- Keep runtime-vs-docs behavior aligned with runtime implementation mode.
- Ensure downstream task execution enforces production code + unit-test evidence for any uncovered runtime gap.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. One-Click Deployment with Smart Defaults | PASS | Quickstart remains executable with existing local Compose stack and documented env token. |
| II. Powerful Runtime Configurability | PASS | Design captures config-driven source-kind and base-capability behavior explicitly. |
| III. Modular and Extensible Architecture | PASS | Contracts remain isolated to manifest modules and queue interfaces. |
| IV. Avoid Exclusive Proprietary Vendor Lock-In | PASS | Provider capabilities remain declarative and non-exclusive. |
| V. Self-Healing by Default | PASS | No design change weakens retry-safe queue semantics. |
| VI. Facilitate Continuous Improvement | PASS | Traceability maps every DOC-REQ to implementation and validation evidence. |
| VII. Spec-Driven Development Is the Source of Truth | PASS | Artifacts now mirror current runtime behavior and strategy constraints. |
| VIII. Skills Are First-Class and Easy to Add | PASS | No additional violations introduced; planning workflow remains skill-driven. |

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T004-T007`, `T010-T012`, `T015-T017`, `T020-T021`) and validation tasks (`T008-T009`, `T013-T014`, `T018-T019`, `T023-T025`) in `tasks.md`.
- `DOC-REQ-*` traceability includes deterministic implementation-task and validation-task mappings for every source requirement (`DOC-REQ-001` through `DOC-REQ-009`) in `contracts/requirements-traceability.md`.
- Cross-artifact determinism is preserved: spec intent (`DOC-REQ-009` runtime authority), plan constraints, and task execution coverage align without contradictory scope language.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls and a `DOC-REQ Coverage Matrix` in `tasks.md` to make runtime/validation gating auditable.
- Added explicit runtime validation evidence requirements in `quickstart.md` for `./tools/test_unit.sh` and runtime scope gates.

### Residual risks

- Branch naming in this workspace (`task/20260302/f5cfebbd-multi`) does not satisfy `.specify` feature-branch prerequisite naming (`NNN-*`), so prerequisite automation depends on fallback/manual checks.
- Future manifest changes that bypass the shared contract can reintroduce drift if unit and scope gates are skipped.

## Complexity Tracking

No constitution violations identified for this planning scope.
