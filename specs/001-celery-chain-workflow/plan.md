# Implementation Plan: Celery Chain Workflow Integration

**Branch**: `001-celery-chain-workflow` | **Date**: 2025-11-12 (updated 2026-02-14) | **Spec**: `specs/001-celery-chain-workflow/spec.md`
**Input**: Feature specification from `/specs/001-celery-chain-workflow/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement a Celery chain that drives Spec Kit phases end-to-end: discover the next actionable task, submit it to Codex Cloud, poll for diffs, and publish GitHub branches/PRs while persisting artifacts and emitting structured status for MoonMind’s UI. Align runtime behavior with the 015 umbrella direction by routing stages through a skills-first policy layer (Speckit default with fallback), keeping Speckit always available on workers, and documenting the fastest startup path for authenticated Codex workers with Gemini embeddings.

## Technical Context

**Language/Version**: Python 3.11 (per AGENTS.md instructions and repo toolchain)  
**Primary Dependencies**: Celery 5.4, RabbitMQ 3.x broker, PostgreSQL (Celery result backend + MoonMind DB), Codex CLI/Cloud, GitHub API, FastAPI service layer  
**Storage**: PostgreSQL schemas `spec_workflow_runs` & `spec_workflow_task_states`, object storage (local `var/artifacts/spec_workflows/<run_id>` as interim)  
**Testing**: `./tools/test_unit.sh` (unit validation gate), plus contract tests for workflow endpoints  
**Target Platform**: Linux containers via docker compose (api, celery-worker, rabbitmq)  
**Project Type**: Backend services (FastAPI API + Celery workers)  
**Performance Goals**: Meet SC-001 (95% workflows reach PR in ≤15 min) & SC-002 (100% task state emission)  
**Constraints**: Deterministic idempotent branch naming (FR-008), structured logging per FR-006, credential validation before execution (FR-010), skills-first policy compatibility with fallback and rollout controls (FR-011 to FR-014)  
**Scale/Scope**: Up to ~5 concurrent Spec workflow runs per deployment (single RabbitMQ node, default classic queues)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Core Principles**: NEEDS CLARIFICATION — `.specify/memory/constitution.md` only lists placeholder headings (`[PRINCIPLE_1_NAME]`, etc.) with no enforceable guidance; in absence of ratified rules, no violations can be evaluated.
- **Additional Constraints / Workflow Rules**: NEEDS CLARIFICATION — sections `[SECTION_2_NAME]` and `[SECTION_3_NAME]` are empty, so there are no extra gates to enforce.

**Gate Status**: PASS WITH NOTE — Constitution file lacks concrete directives; proceeding under assumption that no additional constraints apply until governance is provided.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
```text
api_service/
├── api/routers/workflows.py        # HTTP entrypoints for Spec workflows
├── core/, services/, schemas/      # orchestration + persistence logic
└── scripts/ensure_codex_config.py  # credential enforcement utilities

celery_worker/
├── speckit_worker.py               # Celery worker bootstrap + Codex helpers
└── scripts/codex_login_proxy.py    # Codex CLI auth proxy in worker pods

moonmind/
├── workflows/speckit_celery/       # workflow orchestration + repositories
├── workflows/skills/               # skills-first stage policy and adapter layer
├── config/settings.py              # runtime configuration for queues/backends
└── schemas/workflow_models.py      # serialization of workflow entities

specs/001-celery-chain-workflow/    # Feature docs (spec, plan, research, etc.)

tests/
├── unit/workflows/test_tasks.py    # unit tests for Celery routing + logic
├── unit/workflows/test_skills_runner.py
└── contract/test_workflow_api.py   # API contract coverage for workflow endpoints
```

**Structure Decision**: This feature spans the existing backend/API + Celery worker stack. Implementation touches `api_service`, `celery_worker`, `moonmind/workflows/...`, and supporting tests, with an added `moonmind/workflows/skills/` compatibility layer for skills-first orchestration; documentation continues under `specs/001-celery-chain-workflow/`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
