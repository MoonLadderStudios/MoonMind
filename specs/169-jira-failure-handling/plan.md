# Implementation Plan: Jira Failure Handling

**Branch**: `169-jira-failure-handling` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/169-jira-failure-handling/spec.md`  
**Mode**: Runtime implementation. Deliverables must include production runtime code changes and validation tests, not docs-only changes.

## Summary

Harden Phase 8 Jira Create-page failure handling so Jira remains additive when unavailable. The implementation will normalize Jira browser backend failures into safe structured MoonMind error responses, keep Jira empty states renderable, and surface frontend Jira load failures as inline browser-panel messages without disabling manual task authoring or the existing Create submission flow.

## Technical Context

**Language/Version**: Python 3.12 for FastAPI router/service tests; TypeScript with React for Mission Control Create page behavior.  
**Primary Dependencies**: FastAPI, Pydantic response models, existing Jira browser service/client error model, React, TanStack Query, Testing Library, Vitest.  
**Storage**: N/A. Failure handling and empty states are request/response and transient UI state only. No persisted task payload, Jira provenance, or database schema change is planned.  
**Testing**: `pytest` for Jira browser router/service validation; Vitest/Testing Library for Create page failure isolation; `./tools/test_unit.sh` for final unit verification.  
**Target Platform**: MoonMind API service and browser-hosted Mission Control Create page.  
**Project Type**: Web application feature spanning an existing backend router and existing frontend entrypoint.  
**Performance Goals**: Failure mapping adds no extra Jira requests; UI failure rendering must not block typing, preset editing, or submit interactions.  
**Constraints**: Jira UI remains optional and runtime-config gated; browser clients must never receive raw Jira credentials or provider traces; task submission contract remains unchanged; Jira failures remain local to the browser.  
**Scale/Scope**: Existing Jira browser endpoints, one Create page entrypoint, focused router/service tests, and focused Create page tests for failure isolation and manual creation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. This feature hardens an optional instruction-source integration and does not introduce a new agent runtime or orchestration model.
- **II. One-Click Agent Deployment**: PASS. Jira remains optional; unavailable Jira must not block baseline Create page usage.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior remains isolated behind MoonMind-owned browser endpoints and normalized UI contracts.
- **IV. Own Your Data**: PASS. Jira data remains retrieved through MoonMind boundaries; no browser-to-Jira credential path is introduced.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill execution and task step skill contracts are unchanged.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. The plan strengthens explicit failure contracts rather than relying on implicit exceptions.
- **VII. Powerful Runtime Configurability**: PASS. Jira browser visibility remains governed by existing runtime config and feature rollout.
- **VIII. Modular and Extensible Architecture**: PASS. Work stays in the Jira browser router/service boundary and Create page entrypoint.
- **IX. Resilient by Default**: PASS. The feature exists specifically to make Jira outages non-blocking for manual task creation.
- **X. Facilitate Continuous Improvement**: PASS. Validation captures failure categories and regression cases for future Jira phases.
- **XI. Spec-Driven Development**: PASS. Plan follows `spec.md` and includes traceability for each `DOC-REQ-*`.
- **XII. Canonical Documentation Separation**: PASS. Canonical docs are used as source contracts; migration details stay in feature artifacts.
- **XIII. Pre-Release Velocity**: PASS. No compatibility aliases or duplicate legacy error contracts are introduced.
- **Security / Secret Hygiene**: PASS. Error mapping must sanitize secret-like values and avoid raw provider body exposure.
- **Validation Required**: PASS. The plan defines backend and frontend validation paths plus repo unit verification.

## Project Structure

### Documentation (this feature)

```text
specs/169-jira-failure-handling/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jira-browser-failure.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/
└── jira_browser.py

moonmind/integrations/jira/
├── browser.py
└── errors.py

frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

tests/unit/api/routers/
└── test_jira_browser.py

tests/unit/integrations/
└── test_jira_browser_service.py
```

**Structure Decision**: Keep runtime changes in the existing Jira browser router/service and existing Create page entrypoint because Phase 8 hardens current behavior rather than adding a new surface. Add or extend tests in the current router, service, and Create-page test files.

## Phase 0: Research

See [research.md](./research.md).

Key decisions:

- Use a single structured Jira browser error envelope for known and unexpected backend failures.
- Preserve existing empty list/model responses for successful no-content Jira browser operations.
- Render frontend Jira failures locally in the shared browser panel with manual-continuation guidance.
- Keep the Create submit path and payload shape unchanged.

## Phase 1: Design

See:

- [data-model.md](./data-model.md)
- [contracts/jira-browser-failure.yaml](./contracts/jira-browser-failure.yaml)
- [contracts/requirements-traceability.md](./contracts/requirements-traceability.md)
- [quickstart.md](./quickstart.md)

## Post-Design Constitution Check

- **Runtime intent**: PASS. Design artifacts require production backend/frontend code changes and validation tests.
- **Resiliency**: PASS. Jira failures are explicit and local, while manual task creation remains available.
- **Security**: PASS. Structured error responses include safe messages only and validation requires secret-redaction regression coverage.
- **Runtime configurability**: PASS. No new always-on Jira behavior is introduced.
- **Submission compatibility**: PASS. Existing create endpoint, objective resolution, artifact fallback, and task payload shape remain unchanged.
- **Spec traceability**: PASS. Every `DOC-REQ-*` is mapped to implementation surfaces and validation in `contracts/requirements-traceability.md`.

## Complexity Tracking

No Constitution violations. No additional complexity exceptions are required.
