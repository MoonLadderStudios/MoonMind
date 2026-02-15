# Implementation Plan: Skills-First Spec Automation Pipeline

**Branch**: `002-document-speckit-automation` | **Date**: 2025-11-03 (updated 2026-02-14) | **Spec**: `specs/002-document-speckit-automation/spec.md`  
**Input**: Feature specification from `/specs/002-document-speckit-automation/spec.md`

## Summary

Align the Spec Automation feature with umbrella 015 by keeping legacy `speckit_*` phase compatibility while exposing skills-first execution metadata in runtime models and API responses. Preserve existing `/api/spec-automation/*` behavior, add deterministic metadata defaults for legacy phases, and update docs/contracts for Codex-authenticated worker startup plus Gemini embedding defaults.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, SQLAlchemy models, Pydantic schemas, Celery worker runtime, existing Spec Automation repositories  
**Storage**: Existing `spec_automation_runs`, `spec_automation_task_states`, `spec_automation_artifacts`, `spec_automation_agent_configs` tables  
**Testing**: `./tools/test_unit.sh` (required gate), plus focused unit tests for API serialization and model metadata normalization  
**Target Platform**: Linux Docker Compose runtime (`api`, `rabbitmq`, `celery_codex_worker`, `celery_gemini_worker`)  
**Project Type**: Backend workflow/runtime + API contract alignment  
**Performance Goals**: Preserve existing API response latency while adding lightweight metadata normalization  
**Constraints**: Maintain backward compatibility for legacy phase values and existing API routes; no destructive enum/database changes  
**Scale/Scope**: Runtime model/schema/router updates, tests, and feature docs/contracts under `specs/002-*`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` remains placeholder-only with no enforceable principle text.
- Repository guardrails still apply:
  - runtime code changes are required (not docs-only),
  - validation must use `./tools/test_unit.sh`.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/002-document-speckit-automation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── spec-automation.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/speckit_celery/
├── models.py
├── repositories.py
└── tasks.py

moonmind/schemas/
└── workflow_models.py

api_service/api/routers/
└── spec_automation.py

tests/unit/api/
└── test_spec_automation.py

tests/unit/workflows/
└── test_spec_automation_env.py
```

**Structure Decision**: Keep existing `spec_automation` runtime surfaces and update serialization/model contracts for skills-first metadata normalization without changing endpoint topology.

## Phase 0: Research Plan

1. Confirm compatibility boundaries for existing phase enum values and API contracts.
2. Define normalized skills metadata behavior for legacy phase records with no explicit metadata.
3. Define docs/quickstart alignment for Codex auth + Gemini embedding fast path.

## Phase 1: Design Outputs

- `research.md`: rationale for skills-first compatibility strategy and fallback normalization.
- `data-model.md`: normalized skills metadata representation for `SpecAutomationTaskState`.
- `contracts/spec-automation.openapi.yaml`: phase metadata fields and expanded stage contract coverage.
- `quickstart.md`: deterministic startup path and verification guidance aligned with umbrella 015.

## Post-Design Constitution Re-check

- Runtime-first implementation and tests remain in scope.
- No constitution-level blockers were identified beyond placeholder governance text.

**Gate Status**: PASS.
