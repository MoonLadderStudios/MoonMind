# Implementation Plan: Celery Chain Workflow Integration

**Branch**: `001-celery-chain-workflow` | **Date**: 2025-11-02 | **Spec**: specs/001-celery-chain-workflow/spec.md
**Input**: Feature specification from `/specs/001-celery-chain-workflow/spec.md`

## Summary

Introduce a Spec Kit provider backed by a Celery Chain so MoonMind can discover the next phase, delegate work to Codex Cloud, apply the resulting diff, and publish a GitHub pull request in a single automated run. The change set adds a Celery worker package, persistence for workflow runs, and orchestration tasks that surface structured status to the existing MoonMind UI while reusing Codex CLI and gh automation scripts.

## Technical Context

**Language/Version**: Python 3.11 (matches existing MoonMind services and supported pyproject range)  
**Primary Dependencies**: Celery 5.4, Redis 8 (broker), PostgreSQL (existing MoonMind DB for run persistence), Codex CLI, GitHub CLI  
**Storage**: PostgreSQL `spec_workflow_runs` + `spec_workflow_task_states`; Redis broker for task dispatch; object storage optional for large artifacts (initially local filesystem under `var/artifacts/spec_workflows/<run_id>`)
**Testing**: pytest with celery worker fixtures, contract snapshot tests for API responses  
**Target Platform**: Linux containers (MoonMind API + Celery worker deployed via docker-compose/k8s)  
**Project Type**: API backend with background worker  
**Performance Goals**: Complete 95% of runs within 15 minutes; task status updates available within 5 seconds of state change  
**Constraints**: Must operate within existing MoonMind auth/secret management; use workspace-write sandbox for Codex CLI; avoid introducing new external network paths beyond Codex/GitHub  
**Scale/Scope**: Support 10 concurrent workflow runs with linear scaling via additional Celery workers

## Constitution Check

Current constitution file contains placeholders only; no ratified principles or gates exist. Proceeding under temporary governance with a note to update once the constitution is finalized. No violations recorded.

**Post-Design Revalidation**: Phase 1 outputs introduce Celery modules and database tables without conflicting with any stated (or pending) governance rules. Gate remains clear.

## Project Structure

### Documentation (this feature)

```text
specs/001-celery-chain-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── workflow.openapi.yaml
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── __init__.py
│   ├── speckit_celery/
│   │   ├── __init__.py
│   │   ├── tasks.py
│   │   ├── orchestrator.py
│   │   ├── models.py
│   │   ├── repositories.py
│   │   └── serializers.py
│   └── adapters/
│       ├── codex_client.py
│       └── github_client.py
├── api/
│   └── routers/
│       └── workflows.py
└── schemas/
    └── workflow_models.py

api_service/
├── migrations/
│   └── versions/
│       └── add_spec_workflow_tables.py
└── tests/

celery_worker/
└── speckit_worker.py (entrypoint for dedicated worker queue)

tests/
├── integration/
│   └── workflows/
│       └── test_workflow_chain.py
├── contract/
│   └── test_workflow_api.py
└── unit/
    └── workflows/
        └── test_tasks.py
```

**Structure Decision**: Extend existing MoonMind backend with a new `moonmind.workflows.speckit_celery` module, expose endpoints under `moonmind.api.routers.workflows`, and provision a dedicated Celery worker entrypoint plus database migration for run persistence. Testing follows existing `tests/` layout with unit, contract, and integration coverage.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | – | – |
