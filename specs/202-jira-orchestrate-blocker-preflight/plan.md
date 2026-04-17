# Implementation Plan: Jira Orchestrate Blocker Preflight

**Branch**: `202-jira-orchestrate-blocker-preflight` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/202-jira-orchestrate-blocker-preflight/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not run in this managed workspace because the current branch is `mm-398-e3573b0c`, while the helper requires `###-feature-name` branch naming. Planning artifacts were generated manually from `.specify/templates/plan-template.md` for the active feature directory recorded in `.specify/feature.json`.

## Summary

Add a pre-implementation blocker check to the seeded Jira Orchestrate preset so a target issue that is blocked by a non-Done Jira issue stops before MoonSpec implementation, pull request creation, or Code Review transition. The planned implementation is a narrow preset and test update: add an explicit blocker preflight step that uses trusted Jira tool calls, keeps existing In Progress and Code Review gates intact, and validates the expanded preset and startup seeding behavior with focused unit and integration coverage.

## Technical Context

**Language/Version**: Python 3.12 plus YAML seed templates  
**Primary Dependencies**: Existing task template catalog/seeding service, FastAPI task template surfaces, Pydantic v2 schemas, trusted Jira MCP tool surface, pytest  
**Storage**: Existing task template catalog records only; no new persistent storage  
**Unit Testing**: `pytest` through focused tests and final `./tools/test_unit.sh`  
**Integration Testing**: Existing startup seed synchronization integration test; full hermetic integration via `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind API/control-plane service and managed-agent task preset execution  
**Project Type**: FastAPI control plane with YAML seeded task preset catalog and managed-agent workflow instructions  
**Performance Goals**: Preset expansion remains deterministic and does not add measurable startup or expansion overhead beyond one additional seeded step  
**Constraints**: Use only trusted Jira data and tools; do not use raw Jira credentials or scraping; fail closed when blocker status is unavailable; preserve MM-398 in downstream artifacts  
**Scale/Scope**: One global seeded preset, one preflight step, existing catalog/startup tests, and no database migration

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature composes existing Jira and MoonSpec workflow steps instead of rebuilding agent behavior.
- **II. One-Click Agent Deployment**: PASS. The change is seeded preset data and tests; it adds no mandatory external service beyond optional configured Jira usage.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior stays in the existing trusted Jira integration boundary and does not affect non-Jira workflows.
- **IV. Own Your Data**: PASS. Jira metadata is fetched through MoonMind's trusted tool surface and resulting artifacts remain in the operator workspace.
- **V. Skills Are First-Class and Easy to Add**: PASS. The new guard is modeled as a preset step with explicit input, output, and failure behavior.
- **VI. Evolving Scaffolds**: PASS. The preset remains replaceable YAML composition rather than hardwired cognitive logic.
- **VII. Runtime Configurability**: PASS. Existing Jira issue key and orchestration inputs remain template-driven.
- **VIII. Modular Architecture**: PASS. Changes are isolated to seeded preset data and tests unless implementation discovery finds an existing helper needs a small extension.
- **IX. Resilient by Default**: PASS. The preflight is fail-closed for unresolved or unknowable blockers and prevents dependent work from starting prematurely.
- **X. Continuous Improvement**: PASS. Blocked outcomes must explain the target issue and blocker details for operator follow-up.
- **XI. Spec-Driven Development**: PASS. This spec and plan define the work before task generation and implementation.
- **XII. Documentation Separation**: PASS. Planning remains under `specs/`; no canonical documentation migration narrative is added.
- **XIII. Pre-Release Velocity**: PASS. The plan updates the live preset path and tests directly without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/202-jira-orchestrate-blocker-preflight/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── jira-orchestrate-blocker-preflight.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── data/
│   └── task_step_templates/
│       └── jira-orchestrate.yaml
└── services/
    └── task_templates/
        └── catalog.py

tests/
├── unit/
│   └── api/
│       └── test_task_step_templates_service.py
└── integration/
    └── test_startup_task_template_seeding.py
```

**Structure Decision**: Use the existing seeded task-template path and existing catalog/startup test locations. No new package, database table, route, or runtime service is planned.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design And Contracts

- [data-model.md](./data-model.md) defines Target Jira Issue, Blocking Jira Issue, and Blocker Preflight Outcome.
- [contracts/jira-orchestrate-blocker-preflight.md](./contracts/jira-orchestrate-blocker-preflight.md) defines the preset step contract and expected outcomes.
- [quickstart.md](./quickstart.md) defines red-first validation, focused unit and integration commands, and final verification commands.

## Post-Design Constitution Re-Check

- **I-XIII**: PASS after Phase 1 design. The selected design remains a narrow trusted-preset update with explicit test strategy, no new storage, no raw Jira credential handling, no compatibility shims, and no canonical documentation churn.

## Complexity Tracking

No constitution violations or justified complexity additions.
