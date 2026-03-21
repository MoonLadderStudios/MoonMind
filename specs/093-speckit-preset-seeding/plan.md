# Implementation Plan: Seeded Speckit Preset Availability

**Branch**: `093-speckit-preset-seeding` | **Date**: 2026-03-21 | **Spec**: `specs/093-speckit-preset-seeding/spec.md`
**Input**: Feature specification from `specs/093-speckit-preset-seeding/spec.md`

## Summary

Restore the default `speckit-orchestrate` Mission Control preset by making the task preset catalog synchronize YAML seeds into the database at API startup. The existing YAML seed remains the source of truth for the MoonMind-native step translation; the new work adds an upsert-style sync path for seeded templates, invokes it from startup, and verifies both catalog behavior and startup integration with tests.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, PyYAML  
**Storage**: PostgreSQL/SQLite via existing `task_step_templates` tables  
**Testing**: `./tools/test_unit.sh`  
**Platform**: Docker Compose local stack and test SQLite harness  
**Project Type**: Monorepo web/API service

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The preset remains a thin MoonMind orchestration layer over existing skills.
- **II. One-Click Agent Deployment**: PASS. Startup seeding reduces manual operator setup for the default preset.
- **III. Avoid Vendor Lock-In**: PASS. The preset is YAML-backed and runtime-neutral at the catalog layer.
- **IV. Own Your Data**: PASS. Preset state remains in operator-controlled DB storage.
- **V. Skills Are First-Class and Easy to Add**: PASS. The seed continues to reference first-class skills such as `speckit-specify`.
- **VI. Design for Deletion / Scientific Method**: PASS. Seed sync is a small adapter around existing catalog contracts.
- **VII. Powerful Runtime Configurability**: PASS. Behavior is gated by the existing task preset catalog feature flag.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within catalog service and startup composition boundaries.
- **IX. Resilient by Default**: PASS. Startup sync fails soft when preset tables are unavailable.
- **X. Facilitate Continuous Improvement**: PASS. Tests cover both create and refresh paths to make regressions visible.
- **XI. Spec-Driven Development**: PASS. This plan and tasks accompany the implementation.

## Project Structure

```text
api_service/
├── main.py
└── services/task_templates/catalog.py
tests/
├── integration/test_startup_task_template_seeding.py
└── unit/api/test_task_step_templates_service.py
specs/093-speckit-preset-seeding/
├── spec.md
├── plan.md
└── tasks.md
```

## Implementation Strategy

1. Add a catalog-service synchronization method that reads YAML seed definitions and upserts matching template/version rows.
2. Invoke that synchronization during API startup when the task preset catalog feature is enabled.
3. Keep startup resilient by logging and skipping on missing preset tables.
4. Add targeted unit/integration tests for creation, update, and startup-triggered seeding.

## Complexity Tracking

- No constitutional violations expected.
