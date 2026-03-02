# Implementation Plan: Skills Workflow Alignment Refresh

**Branch**: `015-skills-workflow` | **Date**: 2026-03-02 | **Spec**: `specs/015-skills-workflow/spec.md`
**Input**: Feature specification from `/specs/015-skills-workflow/spec.md`

## Summary

Align `specs/015-skills-workflow` with current MoonMind runtime strategy by using canonical workflow stage names, documenting shared-skills workspace behavior, and preserving runtime/API metadata observability (`selectedSkill`, `adapterId`, `executionPath` to API `selected_skill`, `adapter_id`, `execution_path`).  
The selected orchestration mode for this feature is **runtime implementation**, so planning explicitly includes production code surfaces under `moonmind/` and validation through `./tools/test_unit.sh` instead of docs-only completion.

## Technical Context

**Language/Version**: Python 3.11 runtime target (project range `>=3.10,<3.14`)  
**Primary Dependencies**: FastAPI, Pydantic v2, Celery, SQLAlchemy, Docker SDK  
**Storage**: PostgreSQL (`spec_automation_runs` / `spec_automation_task_states`), filesystem artifacts under `var/artifacts/spec_workflows`  
**Testing**: `./tools/test_unit.sh` (canonical unit-test entrypoint)  
**Target Platform**: Docker Compose services on Linux (`api`, `codex-worker`, `gemini-worker`, `rabbitmq`)  
**Project Type**: Backend monorepo (API + workers + workflow engine)  
**Performance Goals**: No additional DB round-trips for metadata normalization; preserve existing workflow/API latency envelope  
**Constraints**: Preserve backward compatibility for legacy persisted metadata; fail-fast for unsupported skill/adapter combos; keep runtime-vs-docs mode behavior aligned with selected runtime mode  
**Scale/Scope**: Focused refresh of `specs/015-skills-workflow` artifacts and targeted workflow/API metadata surfaces

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Gate

| Principle | Status | Notes |
|-----------|--------|-------|
| I. One-Click Deployment with Smart Defaults | PASS | Quickstart uses current compose services and explicit auth prerequisites. |
| II. Powerful Runtime Configurability | PASS | Stage skill selection and runtime mode remain configuration-driven. |
| III. Modular and Extensible Architecture | PASS | Changes target workflow model normalization + schema/contract layers without cross-cutting rewrites. |
| IV. Avoid Exclusive Proprietary Vendor Lock-In | PASS | Skills-first adapter contract remains explicit (`adapterId` surfaced). |
| V. Self-Healing by Default | PASS | Compatibility defaults for legacy metadata preserve deterministic diagnostics. |
| VI. Facilitate Continuous Improvement | PASS | Structured per-phase metadata remains queryable through API payloads. |
| VII. Spec-Driven Development Is the Source of Truth | PASS | Spec/plan/research/data-model/contracts/quickstart are refreshed together for drift control. |
| VIII. Skills Are First-Class and Easy to Add | PASS | Stage contracts preserve explicit skill and adapter resolution metadata. |

**Gate Result (Pre-Phase 0)**: PASS

### Post-Phase 1 Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. One-Click Deployment with Smart Defaults | PASS | Fast-path docs remain consistent with auth scripts and current service names. |
| II. Powerful Runtime Configurability | PASS | Contracts document conditional Speckit verification based on configured stage skills. |
| III. Modular and Extensible Architecture | PASS | API schema contract and workflow metadata model remain separated by module boundaries. |
| IV. Avoid Exclusive Proprietary Vendor Lock-In | PASS | Adapter metadata is explicit and portable in structured payloads. |
| V. Self-Healing by Default | PASS | Legacy default derivation rules are preserved and testable. |
| VI. Facilitate Continuous Improvement | PASS | Contracted observability fields support triage without raw worker internals. |
| VII. Spec-Driven Development Is the Source of Truth | PASS | `DOC-REQ-*` traceability document maps each requirement to implementation + validation. |
| VIII. Skills Are First-Class and Easy to Add | PASS | Shared skills workspace and stage execution contracts are updated to current runtime behavior. |

**Gate Result (Post-Phase 1)**: PASS

## Project Structure

### Documentation (this feature)

```text
specs/015-skills-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── compose-fast-path.md
│   ├── requirements-traceability.md
│   ├── skills-stage-contract.md
│   └── spec-automation-api.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── spec_automation.py

moonmind/
├── schemas/
│   └── workflow_models.py
└── workflows/
    ├── skills/
    │   ├── registry.py
    │   └── runner.py
    └── speckit_celery/
        ├── models.py
        └── tasks.py

tests/
└── unit/
    ├── api/
    │   └── test_spec_automation.py
    └── workflows/
        ├── test_spec_automation_env.py
        └── test_tasks.py

tools/
└── test_unit.sh
```

**Structure Decision**: Use the existing backend monorepo structure, with workflow normalization in `moonmind/workflows/speckit_celery/`, API schema/serialization in `moonmind/schemas/` and `api_service/api/routers/`, and regression validation in `tests/unit/`.

## Complexity Tracking

No constitution violations identified for this plan.
