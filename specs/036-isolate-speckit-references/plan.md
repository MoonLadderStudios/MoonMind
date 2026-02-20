# Implementation Plan: Isolate Spec Kit References and Skill-First Runtime

**Branch**: `036-isolate-speckit-references` | **Date**: 2026-02-20 | **Spec**: `specs/036-isolate-speckit-references/spec.md`  
**Input**: Feature specification from `/specs/036-isolate-speckit-references/spec.md`

## Summary

Refactor workflow execution so skill adapters are the authoritative execution path, remove mandatory Speckit checks from non-speckit runtime flows, and introduce neutral workflow API/config naming with backwards-compatible SPEC aliases. The implementation will keep data persistence stable, add explicit unsupported-skill failures, and add telemetry/logging for legacy alias usage during migration.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI routing (`api_service`), Pydantic settings (`moonmind.config.settings`), Celery worker startup modules (`celery_worker/*`), workflow execution modules (`moonmind/workflows/skills/*`, `moonmind/workflows/speckit_celery/tasks.py`).  
**Storage**: Existing PostgreSQL workflow tables remain unchanged (`spec_workflow_runs`, `spec_workflow_task_states`).  
**Testing**: `./tools/test_unit.sh` (required unit test entrypoint), focused updates to workflow skills, codex worker preflight, and workflow API contract tests.  
**Target Platform**: Docker Compose services and local dev runtime for API + worker modules.  
**Project Type**: Monorepo Python backend (API + Celery + worker entrypoints).  
**Performance Goals**: No measurable regression in workflow startup or request handling; adapter resolution must fail fast before stage execution.  
**Constraints**: Preserve existing workflow storage schema, preserve legacy SPEC-prefixed API compatibility during migration, avoid requiring Speckit when selected skill/runtime does not need it.  
**Scale/Scope**: Applies to all spec workflow stages (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`), shared worker startup checks, and public workflow API routing.

## Constitution Check

`.specify/memory/constitution.md` is still a placeholder template with no enforceable principles. No hard gates can be derived; implementation proceeds under existing repository norms (compatibility-preserving migrations, explicit test coverage, and runtime safety checks).

## Project Structure

### Documentation (this feature)

```text
specs/036-isolate-speckit-references/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── workflow-runs-api.md
│   └── skill-adapter-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── config/settings.py
├── workflows/skills/
│   ├── contracts.py
│   ├── registry.py
│   ├── runner.py
│   └── resolver.py
├── workflows/speckit_celery/tasks.py
└── agents/codex_worker/cli.py

celery_worker/
├── speckit_worker.py
└── gemini_worker.py

api_service/
├── api/routers/workflows.py
└── config.template.toml

tests/
├── contract/test_workflow_api.py
├── unit/agents/codex_worker/test_cli.py
└── unit/workflows/
   ├── test_skills_runner.py
   └── test_skills_resolver.py

.env-template
```

**Structure Decision**: Keep behavior changes localized to existing workflow/worker/router modules instead of introducing new packages. Add compatibility aliases and deprecation instrumentation in-place so callers can migrate incrementally.

## Phase 0 – Research Summary

See `specs/036-isolate-speckit-references/research.md`. Key decisions:

1. Use an adapter registry map with explicit erroring for unsupported skills (no silent direct fallback path for unknown adapters).
2. Gate Speckit executable verification by effective selected skills, not by unconditional startup/task checks.
3. Provide canonical workflow API route prefix (`/api/workflows/runs`) while retaining `/api/workflows/speckit` as deprecated compatibility alias.
4. Keep SPEC-prefixed settings/structures for compatibility, but introduce neutral aliases where practical and deprecation signaling.

## Phase 1 – Design Outputs

- **Data Model** (`data-model.md`): Defines runtime entities for adapter bindings, dependency checks, and legacy alias usage telemetry.
- **Contracts**:
  - `contracts/workflow-runs-api.md` for canonical + legacy route behavior and deprecation headers.
  - `contracts/skill-adapter-contract.md` for adapter resolution semantics and unsupported-skill errors.
- **Quickstart** (`quickstart.md`): Validation commands for adapter execution, startup behavior without Speckit, and API alias checks.
- No `DOC-REQ-*` IDs exist in `spec.md`; requirements-traceability matrix is not required.

## Implementation Strategy

### US1 – Skill-first execution without mandatory Speckit dependency

- Update `moonmind/workflows/skills/registry.py` to make adapter selection explicit and extensible.
- Update `moonmind/workflows/skills/runner.py` to:
  - execute through resolved adapters,
  - fail fast for skills without adapters,
  - preserve direct fallback only for adapter execution failures when configured.
- Update `moonmind/workflows/skills/resolver.py` to remove hardcoded builtin source fallback for speckit and require resolvable sources.
- Add/adjust unit tests in `tests/unit/workflows/test_skills_runner.py` and `tests/unit/workflows/test_skills_resolver.py`.

### US2 – Neutral workflow naming with backward compatibility

- Add canonical workflow API endpoints in `api_service/api/routers/workflows.py` while preserving existing `/api/workflows/speckit/*` paths as deprecated aliases.
- Add legacy-route usage logging plus response deprecation headers for migration observability.
- Add API contract coverage for canonical and legacy endpoints in `tests/contract/test_workflow_api.py`.
- Update `api_service/config.template.toml` and `.env-template` comments/defaults to document canonical naming and legacy alias behavior.

### US3 – Explicit failure behavior and dependency checks

- Refactor Speckit CLI checks in:
  - `moonmind/agents/codex_worker/cli.py`
  - `celery_worker/speckit_worker.py`
  - `celery_worker/gemini_worker.py`
  - `moonmind/workflows/speckit_celery/tasks.py`
  so checks run only when selected/allowed skills require Speckit.
- Ensure non-speckit paths can start and run without Speckit installed.
- Add/adjust tests in `tests/unit/agents/codex_worker/test_cli.py` for runtime-specific preflight behavior.

## Risks & Mitigations

- **Risk**: Legacy clients depend on existing `/api/workflows/speckit` routes.  
  **Mitigation**: Keep route compatibility and mark deprecated with explicit response headers and logs.
- **Risk**: Removing builtin source fallback could break environments without mirrored Speckit skill content.  
  **Mitigation**: Keep configuration-driven skill source overrides and clear error messaging for missing sources.
- **Risk**: Startup checks diverge between worker runtimes.  
  **Mitigation**: Centralize skill dependency decision logic and cover codex/gemini runtime paths in unit tests.

## Next Steps

1. Generate `tasks.md` with dependency-ordered implementation and validation tasks.
2. Run runtime scope gate against tasks.
3. Execute implementation and test updates via `./tools/test_unit.sh`.
