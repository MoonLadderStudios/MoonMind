# Implementation Plan: Skills-First Workflow Umbrella

**Branch**: `015-skills-workflow` | **Date**: 2026-02-14 | **Spec**: `specs/015-skills-workflow/spec.md`
**Input**: Feature specification from `/specs/015-skills-workflow/spec.md`

## Summary

Adopt a skills-first workflow architecture where workers always include Speckit capability, workflow stages execute through stage contracts with allowlisted skill selection, and direct implementations remain as fallbacks. Deliver the fastest operator path for authenticated Codex workers and Google Gemini embeddings through compose/runtime/documentation alignment.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Celery 5.4, RabbitMQ 3.x, PostgreSQL, Pydantic settings, Codex CLI, Spec Kit CLI (`speckit`), Gemini CLI, existing MoonMind workflow modules  
**Storage**: Existing workflow persistence tables and artifact roots (`var/artifacts/workflow_runs`, optional `var/artifacts/agent_jobs`)  
**Testing**: Unit tests via `./tools/test_unit.sh`; targeted integration/smoke verification via Docker Compose service startup checks  
**Target Platform**: Linux Docker Compose runtime for API + Celery workers  
**Project Type**: Backend workflow orchestration and worker runtime evolution with documentation/runbook updates  
**Performance Goals**: No material increase in worker startup latency; stage routing overhead remains bounded to lightweight skill-selection and dispatch checks  
**Constraints**: Preserve queue compatibility (`speckit`, `codex`, `gemini`), preserve existing API behavior during migration, keep Speckit always available, avoid interactive auth in worker runtime  
**Scale/Scope**: Workflow orchestration path updates, worker startup checks, settings/flags, documentation, and test coverage for parity/fallback/rollout

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains placeholder text and no enforceable MUST/SHOULD clauses.
- Repository constraints from `AGENTS.md` are explicitly enforced here:
  - unit validation uses `./tools/test_unit.sh`;
  - runtime implementation (not docs-only) is required for execution phases.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/015-skills-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── skills-stage-contract.md
│   └── compose-fast-path.md
└── tasks.md
```

### Source Code (repository root)

```text
celery_worker/
├── speckit_worker.py
└── gemini_worker.py

moonmind/config/
└── settings.py

moonmind/workflows/
├── __init__.py
├── speckit_celery/
│   ├── models.py
│   ├── orchestrator.py
│   ├── services.py
│   └── tasks.py
└── skills/                        # new workflow skills adapter package
    ├── __init__.py
    ├── contracts.py
    ├── registry.py
    ├── runner.py
    └── speckit_adapter.py

api_service/api/routers/
└── workflows.py

docs/
├── CodexCliWorkers.md
└── SpecKitAutomationInstructions.md

README.md
docker-compose.yaml

tests/unit/workflows/
├── test_tasks.py
├── test_workflow_env.py
├── test_worker_entrypoints.py    # new
└── test_skills_runner.py          # new

tests/unit/agents/codex_worker/
└── test_cli.py
```

**Structure Decision**: Keep existing workflow modules as compatibility surfaces, introduce a dedicated `moonmind/workflows/skills/` adapter layer for stage contract execution and fallback logic, and avoid disruptive API/queue renames in this migration.

## Phase 0: Research Plan

1. Define stage-contract boundaries that preserve current behavior while enabling skills-first dispatch.
2. Decide skill registry/allowlist model and override precedence (global default, per-stage, per-run).
3. Define rollout controls (shadow/canary/fallback) with minimal operational complexity.
4. Define startup prerequisite checks for Speckit availability, Codex auth readiness, and Google embedding prerequisites.
5. Define the minimal compose and README updates that preserve "fastest path" operator experience.

## Phase 1: Design Outputs

- `research.md`: decisions, alternatives, and rationale for skills-first adapter architecture and rollout.
- `data-model.md`: runtime value objects/enums for stage contract execution, path metadata, and startup capability profiles.
- `contracts/skills-stage-contract.md`: canonical stage input/output contract and fallback semantics.
- `contracts/compose-fast-path.md`: runtime env and docker-compose contract for Codex auth + Gemini embedding fast path.
- `quickstart.md`: deterministic startup and verification flow for operators.

## Post-Design Constitution Re-check

- Design maintains runtime-first implementation intent and test validation requirements.
- No enforceable constitution conflicts were identified beyond placeholder constitution content.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
