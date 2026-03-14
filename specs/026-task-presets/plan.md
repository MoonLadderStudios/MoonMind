# Implementation Plan: Task Presets Strategy Alignment

**Branch**: `024-task-presets` | **Date**: 2026-03-01 | **Spec**: `specs/024-task-presets/spec.md`  
**Input**: Feature specification from `/specs/024-task-presets/spec.md`

## Summary

Align the seeded global `agentkit-orchestrate` preset with MoonMind publish-stage strategy by keeping runtime execution in report handoff mode, synchronizing existing DB seed rows with current YAML content through an idempotent migration, and locking behavior with regression tests. Runtime versus docs orchestration mode behavior remains explicit through `inputs.orchestration_mode` propagation and mode-aware scope validation gates.

## Technical Context

**Language/Version**: Python 3.11 + YAML seed definitions.  
**Primary Dependencies**: FastAPI router/services, SQLAlchemy + Alembic migrations, PyYAML seed parsing, pytest unit tests.  
**Storage**: PostgreSQL tables `task_step_templates` and `task_step_template_versions` (data refresh only, no schema change).  
**Testing**: `./tools/test_unit.sh` with regression coverage in `tests/unit/api/test_task_template_seed_alignment.py` and existing task template service/router tests.  
**Target Platform**: Dockerized MoonMind API service + worker environments.  
**Project Type**: Backend service with seeded orchestration template documents.  
**Performance Goals**: Preserve current template expansion latency; migration remains one-time and no-op safe when data is absent.  
**Constraints**: Keep runtime/docs orchestration behavior mode-aligned, do not introduce runtime publish actions (commit/push/PR) from preset instructions, keep migration idempotent and best-effort on missing seed/rows.  
**Scale/Scope**: Single seeded preset (`agentkit-orchestrate`, version `1.0.0`) and its existing global template/version records.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I (one-click deployment): PASS. Migration is safe when seed file/rows are missing and does not block environment upgrades.
- Principle II (runtime configurability): PASS. `orchestration_mode` remains explicit input and mode branching is preserved in instructions/gates.
- Principle III (modular architecture): PASS. Changes stay within seed data, one migration file, and focused tests.
- Principle IV (avoid exclusive proprietary vendor lock-in): PASS. Preset behavior is stored in portable YAML and execution remains adapter-driven rather than tied to a single proprietary runtime.
- Principle V (self-healing/idempotence): PASS. Alignment migration refreshes existing data and no-ops when prerequisites are absent.
- Principle VI (continuous improvement): PASS. Final preset step requires a structured report handoff.
- Principle VII (spec-driven development): PASS. Plan, research, data model, contracts, and tasks remain in sync with implementation paths.
- Principle VIII (skills first-class): PASS. Preset continues to orchestrate skill chain without runtime-specific hidden transforms.

Post-Phase-1 re-check: PASS, no additional constitution violations introduced by design artifacts.

## Project Structure

### Documentation (this feature)

```text
specs/024-task-presets/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-step-templates.yaml
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── data/task_step_templates/
│   └── agentkit-orchestrate.yaml
├── migrations/versions/
│   └── 202603010001_align_agentkit_orchestrate_publish_stage.py
├── services/task_templates/
│   └── catalog.py
└── api/routers/
    └── task_step_templates.py

.specify/scripts/bash/
└── validate-implementation-scope.sh

tests/unit/api/
├── test_task_template_seed_alignment.py
├── test_task_step_templates_service.py
└── routers/test_task_step_templates.py
```

**Structure Decision**: Keep this feature focused on existing task template backend surfaces: seed definition, migration alignment, and regression coverage. No new modules or schema tables are required.

No `DOC-REQ-*` IDs are defined in `spec.md`; therefore `contracts/requirements-traceability.md` is not required for this feature.

## Complexity Tracking

No constitution violations or exceptional complexity added by this plan.
