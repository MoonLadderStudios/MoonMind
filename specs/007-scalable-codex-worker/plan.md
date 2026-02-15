# Implementation Plan: Scalable Codex Worker (015-Aligned)

**Branch**: `007-scalable-codex-worker` | **Date**: 2026-02-14 | **Spec**: `specs/007-scalable-codex-worker/spec.md`
**Input**: Feature specification from `/specs/007-scalable-codex-worker/spec.md`

## Summary

Align Codex worker runtime behavior to the 015 umbrella by keeping Speckit always available, preserving skills-first stage metadata semantics, and hardening startup checks for authenticated Codex execution plus Google Gemini embedding readiness.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Celery 5.4, RabbitMQ 3.x, PostgreSQL result backend, Codex CLI, Speckit CLI, Gemini CLI, Pydantic settings  
**Storage**: Docker volumes (`codex_auth_volume`, `gemini_auth_volume`), workflow artifacts under `var/artifacts/spec_workflows/<run_id>`  
**Testing**: Unit tests via `./tools/test_unit.sh`  
**Target Platform**: Docker Compose (API + Celery workers)  
**Project Type**: Backend worker runtime + workflow orchestration compatibility + spec artifacts  
**Performance Goals**: Preserve startup latency while ensuring fail-fast diagnostics for missing prerequisites  
**Constraints**: Maintain queue/API compatibility (`speckit`, `codex`, `gemini`; `/api/workflows/speckit/*`) and non-interactive execution  
**Scale/Scope**: Worker startup checks, skills metadata compatibility, compose/quickstart contract alignment

## Constitution Check

- `.specify/memory/constitution.md` is template-only and does not add enforceable MUST/SHOULD clauses.
- Repository constraints from `AGENTS.md` are applied:
  - Runtime implementation must include production code changes.
  - Unit tests validated via `./tools/test_unit.sh`.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/007-scalable-codex-worker/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── spec.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
celery_worker/
├── speckit_worker.py
├── gemini_worker.py
└── startup_checks.py            # shared startup profile + embedding preflight helper

moonmind/workflows/skills/
├── contracts.py
├── registry.py
└── runner.py

tests/unit/workflows/
├── test_tasks.py
├── test_skills_runner.py
└── test_worker_entrypoints.py
```

**Structure Decision**: Keep existing workflow execution topology and API contracts. Add shared worker startup validation helper to avoid duplicated readiness logic and keep fail-fast behavior deterministic.

## Phase 0: Research Plan

1. Validate which 007 requirements conflict with 015 umbrella semantics.
2. Confirm current runtime already emits skills metadata for discover/submit/publish task payloads.
3. Define minimal startup checks required for Google embedding fast-path diagnostics.
4. Define quickstart updates for one-time Codex auth and queue verification.

## Phase 1: Design Outputs

- `research.md`: documented reconciliation decisions between 007 and 015 umbrella semantics.
- `data-model.md`: startup and stage metadata entities aligned with current runtime behavior.
- `quickstart.md`: deterministic startup and verification path for codex+gemini workers.
- `tasks.md`: completion-tracked runtime/docs work plan.

## Post-Design Constitution Re-check

- Runtime implementation and test expectations remain satisfied.
- No constitution conflicts were introduced.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
