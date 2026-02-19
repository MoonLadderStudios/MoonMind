# Implementation Plan: Task Presets Catalog

**Branch**: `025-task-presets` | **Date**: 2026-02-18 | **Spec**: specs/025-task-presets/spec.md
**Input**: Feature specification from `/specs/025-task-presets/spec.md`

## Summary

Implement a server-hosted Task Preset Catalog that stores curated step templates, exposes REST endpoints to browse/version/expand presets, tracks applied metadata on queued tasks, and empowers the Task Queue UI plus CLI/MCP clients with deterministic compile-time expansion. Work spans five surfaces: (1) DB schema + Alembic migrations to persist templates and their immutable versions, (2) FastAPI services/routers to list/fetch/expand/save templates with validation + audit, (3) seed + governance utilities so ops can ship global defaults, (4) Task Dashboard UI updates for browsing, previewing, applying, grouping, and diffing presets, and (5) secret-aware “save these steps as a template” writer that promotes existing drafts back into the catalog with RBAC + telemetry enforcement.

## Technical Context

**Language/Version**: Python 3.11 (FastAPI backend), Vanilla ES2020 modules bundled into `dashboard.js`, SQL (PostgreSQL 15).  
**Primary Dependencies**: FastAPI, SQLAlchemy + Alembic, Pydantic v2, Celery task payload compiler helpers, in-browser dashboard utilities already bundled in `api_service/static/task_dashboard/dashboard.js`.  
**Storage**: PostgreSQL via SQLAlchemy declarative models (new `task_step_templates` and `task_step_template_versions` tables). Seed defaults live as YAML inside `api_service/data/task_step_templates/`.  
**Testing**: Pytest suites invoked via `./tools/test_unit.sh` (reuses FastAPI test client + database fixtures) plus lightweight DOM/unit coverage for dashboard JS (existing tests under `tests/task_dashboard`).  
**Target Platform**: Dockerized API service with Uvicorn + Postgres; dashboard JS served via FastAPI templates + static bundle to modern Chromium/Edge browsers.  
**Project Type**: Multi-service backend with server-rendered SPA shell; no standalone frontend build pipeline.  
**Performance Goals**: Catalog listing P95 < 200 ms, expansion P95 < 300 ms including validation, UI interactions responsive under 100 ms for preset insertion, audit writes non-blocking to queue submission.  
**Constraints**: Must keep worker payload schema unchanged, forbid runtime/git/publish overrides per task-step contract, secret scrubbing is mandatory for save-as-template, API gated by existing RBAC + rate limits, deterministic step IDs required for reproducibility.  
**Scale/Scope**: Initial catalog < 200 templates with ≤25 steps each; expect ~200 template expansions/day and ≤10 concurrent UI authors.

## Constitution Check

- Constitution document `.specify/memory/constitution.md` currently contains placeholders and no enforced clauses; defaulting to PASS while recording that spec + plan already demand runtime code + validation deliverables.  
- Guardrails honored in this plan: deliver production code (API + UI), cover flows with automated tests, document governance.  
- Outcome: **PASS (no actionable clauses defined yet)**; will re-check after Phase 1 when constitution gains concrete rules.

## Project Structure

```text
specs/025-task-presets/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── task-step-templates.yaml
api_service/
├── api/
│   ├── routers/
│   │   └── task_step_templates.py        # new router for catalog CRUD + expand
│   ├── schemas/
│   │   └── task_step_templates.py        # pydantic I/O models
│   └── dependencies/task_templates.py    # RBAC + capability helpers (new)
├── services/
│   └── task_templates/
│       ├── catalog.py                    # expansion + validation pipeline
│       └── save.py                       # save-from-task helpers
├── db/models.py                          # SQLAlchemy models for templates
├── migrations/versions/
│   └── 024_task_step_templates.py        # Alembic migration + seed hook
├── data/task_step_templates/*.yaml       # seed defaults bundled with image
├── static/task_dashboard/dashboard.js    # UI preset browser + save flow
├── templates/task_dashboard.html         # inject preset drawer mountpoints
└── tests/api_service/
    └── test_task_step_templates.py       # endpoint + validator tests
moonmind/
└── workflows/tasks/payload.py            # union template capabilities when compiling tasks
```

**Structure Decision**: Reuse existing FastAPI monolith; add focused subpackages (`services/task_templates`, `api/schemas/task_step_templates.py`) to isolate catalog logic. Dashboard JS remains a single bundle, so new UI components live inside `dashboard.js` alongside existing submission editor utilities. Tests co-locate within `tests/api_service/` using current fixtures.

## Complexity Tracking

No constitution violations or extraordinary architectural deviations identified; table intentionally empty.
