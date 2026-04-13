# Implementation Plan: Jira Import Actions

**Branch**: `164-jira-import-actions` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/164-jira-import-actions/spec.md`

## Summary

Add explicit Jira import actions to the existing Create page Jira browser so operators can copy normalized Jira issue text into either the preset objective field or a selected step's instructions. The implementation will extend the existing browser shell, target state, issue preview data, preset objective state, and step update path without changing task submission contracts or exposing Jira credentials to the browser.

## Technical Context

**Language/Version**: TypeScript with React 19 for Mission Control UI; Python 3.12 remains unchanged for backend/runtime config already delivered in earlier phases.  
**Primary Dependencies**: React, TanStack Query, Vite/Vitest, Testing Library; existing MoonMind Jira browser endpoints and dashboard runtime config.  
**Storage**: Client-side Create page draft state only. No persisted Jira provenance or task payload storage changes in this phase.  
**Testing**: Vitest/Testing Library for Create page behavior; frontend typecheck and ESLint; repo unit wrapper for final targeted validation.  
**Target Platform**: Mission Control Create Task page served by MoonMind API and rendered in browser.  
**Project Type**: Web application feature in an existing frontend entrypoint with no new backend endpoint contract.  
**Performance Goals**: Import actions complete immediately after issue detail is already loaded; no additional network request is required for Replace or Append.  
**Constraints**: Jira remains optional and additive; manual task creation must continue if Jira fails; browser clients must use MoonMind-owned Jira data only; task submission payload shape must remain unchanged.  
**Scale/Scope**: One shared Jira browser surface, one selected issue at a time, one import target per action, four import modes, and two write actions.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. This feature imports instructions into existing task composition; it does not introduce a new agent runtime or cognitive engine.
- **II. One-Click Agent Deployment**: PASS. Jira UI remains feature-gated and optional; no new mandatory external dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior stays in the existing optional Jira integration and normalized browser read model. The Create page continues to edit MoonMind-native task fields.
- **IV. Own Your Data**: PASS. Imported content is copied into operator-controlled MoonMind task drafts and submitted through the existing MoonMind path.
- **V. Skills Are First-Class and Easy to Add**: PASS. Step instructions and skill controls remain unchanged; Jira import does not introduce a competing skill model.
- **VI. Design for Deletion / Thick Contracts**: PASS. The implementation is constrained to the browser import surface and existing field contracts, making it removable without changing backend task semantics.
- **VII. Powerful Runtime Configurability**: PASS. Jira controls remain governed by runtime config and disabled by default unless explicitly enabled.
- **VIII. Modular and Extensible Architecture**: PASS. Work stays inside the Create page UI boundary and uses already-normalized Jira issue detail data.
- **IX. Resilient by Default**: PASS. Jira failures remain local to the browser and do not block manual task creation.
- **X. Facilitate Continuous Improvement**: PASS. Validation tests cover import behavior and failure fallback.
- **XI. Spec-Driven Development**: PASS. This plan is derived from `spec.md` and will produce tasks before implementation.
- **XII. Canonical Documentation Separation**: PASS. No canonical docs are being changed for this runtime feature.
- **XIII. Pre-Release Velocity**: PASS. The implementation should replace the Phase 4 placeholder copy with final import behavior rather than preserving an obsolete disabled action path.

## Project Structure

### Documentation (this feature)

```text
specs/164-jira-import-actions/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-jira-import.yaml
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

api_service/api/routers/
└── task_dashboard_view_model.py

tests/unit/api/routers/
└── test_task_dashboard_view_model.py
```

**Structure Decision**: Implement the runtime behavior in `frontend/src/entrypoints/task-create.tsx` because the Create page already owns Jira browser state, preset objective state, step state, issue loading, and submission assembly. Add validation in `frontend/src/entrypoints/task-create.test.tsx`; backend runtime config files are listed as existing dependencies and should not change unless implementation discovers that Phase 4 config contracts are incomplete.

## Phase 0 Research

See [research.md](./research.md).

## Phase 1 Design

See [data-model.md](./data-model.md), [quickstart.md](./quickstart.md), and [contracts/create-page-jira-import.yaml](./contracts/create-page-jira-import.yaml).

## Post-Design Constitution Check

- **Runtime intent**: PASS. The design explicitly requires production Create page code changes and validation tests.
- **Submission compatibility**: PASS. Jira import changes authored field text only; no task payload schema change is planned.
- **Security boundary**: PASS. The browser continues to consume MoonMind-owned Jira browser responses and never receives Jira credentials.
- **Failure isolation**: PASS. Jira errors remain local to the browser and do not disable manual authoring or Create.
- **Spec traceability**: PASS. Each functional requirement maps to either import action behavior, target semantics, failure isolation, or validation coverage in the generated design artifacts.

## Complexity Tracking

No constitution violations require justification.
