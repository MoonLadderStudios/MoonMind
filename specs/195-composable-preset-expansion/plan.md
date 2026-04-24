# Implementation Plan: Composable Preset Expansion

**Branch**: `195-composable-preset-expansion` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md) 
**Input**: Single-story feature specification from `specs/195-composable-preset-expansion/spec.md`

## Summary

Implement MM-383 by extending the task template catalog expansion service to treat preset includes as compile-time control-plane composition. Preset versions will accept concrete steps and include entries, recursively resolve active child versions into the existing flattened step output, attach provenance and composition metadata, enforce scope/cycle/limit/input rules, and document the executor boundary. Unit tests cover expansion semantics and failure modes; service-level tests exercise the catalog boundary used by API routes and seed synchronization.

## Technical Context

**Language/Version**: Python 3.12 
**Primary Dependencies**: FastAPI service layer, SQLAlchemy async sessions, Pydantic v2 schemas, Jinja2 `SandboxedEnvironment`, existing task template catalog models 
**Storage**: Existing `TaskStepTemplate` and `TaskStepTemplateVersion` rows with JSON step payloads; no new tables 
**Unit Testing**: pytest via `./tools/test_unit.sh`, focused iteration with `pytest tests/unit/api/test_task_step_templates_service.py` 
**Integration Testing**: Existing service-boundary tests through pytest; full hermetic integration via `./tools/test_integration.sh` when Docker is available 
**Target Platform**: MoonMind API service and managed-agent Linux runtime 
**Project Type**: Web service control plane with Temporal plan execution downstream 
**Performance Goals**: Expansion remains deterministic and bounded by `max_step_count`; recursive include traversal rejects cycles before unbounded work 
**Constraints**: No new persistent storage; global presets cannot include personal presets; nested semantics must resolve before execution-facing plan output; preserve concrete-step-only compatibility 
**Scale/Scope**: One independently testable task preset composition story covering the catalog expansion service, API response metadata, tests, and canonical docs

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Composition stays in the orchestration control plane and does not replace agents or executor behavior.
- II. One-Click Agent Deployment: PASS. No new required services or secrets.
- III. Avoid Vendor Lock-In: PASS. The feature is catalog-local and does not bind to a provider.
- IV. Own Your Data: PASS. Composition metadata remains in local catalog and expansion outputs.
- V. Skills Are First-Class and Easy to Add: PASS. Presets compose existing skill-backed steps without mutating skill contracts.
- VI. Replaceable Scaffolding: PASS. The behavior is implemented behind the existing catalog service boundary with tests.
- VII. Runtime Configurability: PASS. Existing catalog inputs and limits remain operator-controlled through stored preset versions.
- VIII. Modular Architecture: PASS. Changes are scoped to task template catalog service, schemas as needed, docs, and tests.
- IX. Resilient by Default: PASS. Recursive expansion is bounded, deterministic, and fails fast on invalid include graphs.
- X. Continuous Improvement: PASS. Errors identify include paths and verification artifacts preserve MM-383 traceability.
- XI. Spec-Driven Development: PASS. This spec, plan, tasks, implementation, and verification artifacts drive the change.
- XII. Canonical Documentation Separation: PASS. Desired-state docs are updated in `docs/Tasks/TaskPresetsSystem.md`; volatile orchestration input stays in `local-only handoffs`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases are introduced; existing concrete-step expansion remains directly supported.

## Project Structure

### Documentation (this feature)

```text
specs/195-composable-preset-expansion/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── task-template-composition.md
├── tasks.md
└── checklists/
 └── requirements.md
```

### Source Code (repository root)

```text
api_service/
├── services/
│ └── task_templates/
│ └── catalog.py
└── api/
 └── schemas.py

docs/
└── Tasks/
 └── TaskPresetsSystem.md

tests/
└── unit/
 └── api/
 └── test_task_step_templates_service.py
```

**Structure Decision**: Extend the existing task template catalog service because it already owns preset validation, seed synchronization, input resolution, deterministic step IDs, expansion responses, recents, and capability aggregation.

## Complexity Tracking

No constitution violations.
