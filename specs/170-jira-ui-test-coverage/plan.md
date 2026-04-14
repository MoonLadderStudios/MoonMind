# Implementation Plan: Jira UI Test Coverage

**Branch**: `170-jira-ui-test-coverage` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/170-jira-ui-test-coverage/spec.md`  
**Mode**: Runtime implementation. Deliverables must include production runtime code changes where validation exposes behavior gaps, plus validation tests; docs-only output is insufficient.

## Summary

Add Phase 9 validation coverage for the Jira Create-page integration and use the tests to guard existing runtime behavior. The plan expands focused frontend Create page tests, backend Jira browser router tests, Jira browser service normalization tests, and runtime config tests. Production runtime code changes are in scope only when the new coverage reveals a mismatch with `docs/UI/CreatePage.md` or the feature specification.

## Technical Context

**Language/Version**: Python 3.12 for backend services and unit tests; TypeScript with React 19 for Mission Control Create page validation.  
**Primary Dependencies**: FastAPI, Pydantic, MoonMind Jira integration services, React, TanStack Query, Vite/Vitest, Testing Library, pytest.  
**Storage**: No durable storage changes planned. Tests use in-memory fixtures and mocked browser/service responses.  
**Testing**: `pytest` for backend router/service/runtime config coverage; Vitest and Testing Library for Create page behavior; `./tools/test_unit.sh` for final required unit verification.  
**Target Platform**: MoonMind API service and browser-hosted Mission Control Create page.  
**Project Type**: Existing web application with Python backend and TypeScript frontend.  
**Performance Goals**: Test additions should remain focused and deterministic; Create page targeted suite should stay suitable for local iteration, and backend tests should avoid live provider credentials.  
**Constraints**: Jira remains optional and additive; browser-visible Jira calls must stay MoonMind-owned; no live Jira credentials in required tests; existing Create page submission contract must remain unchanged; runtime intent requires production code fixes if tests expose gaps.  
**Scale/Scope**: One feature's test coverage across runtime config, backend Jira browser path, service normalization, frontend browsing/import behavior, failure isolation, and unchanged submission shape.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature validates the existing Jira instruction-source flow and does not introduce a new agent runtime or task model.
- **II. One-Click Agent Deployment**: PASS. Required tests use hermetic fixtures and do not add mandatory Jira credentials or external services to baseline development.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior remains isolated in the optional integration boundary; validation confirms the Create page works through normalized MoonMind models.
- **IV. Own Your Data**: PASS. Tests verify Jira text is copied into MoonMind-owned task draft fields and submitted through existing MoonMind task creation paths.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill model changes are planned; step instruction and skill controls remain existing Create page surfaces.
- **VI. Design for Deletion / Thick Contracts**: PASS. Coverage focuses on documented contracts and makes the Jira browser integration safer to refactor or delete later.
- **VII. Powerful Runtime Configurability**: PASS. Runtime config gating is an explicit validation target.
- **VIII. Modular and Extensible Architecture**: PASS. Work stays inside existing Create page, Jira browser router/service, runtime config builder, and their test boundaries.
- **IX. Resilient by Default**: PASS. Failure isolation and manual task creation after Jira errors are explicit validation targets.
- **X. Facilitate Continuous Improvement**: PASS. The feature improves regression signals for a complex user-facing flow.
- **XI. Spec-Driven Development**: PASS. Plan derives from `spec.md`; DOC-REQ traceability is generated below.
- **XII. Canonical Documentation Separation**: PASS. Canonical docs are used as requirements input; migration notes are not added to canonical docs.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility aliases or hidden fallback transforms are planned; tests should enforce current contracts directly.
- **Security / Secret Hygiene**: PASS. Secret/redaction behavior on the browser API path is an explicit validation target.

## Project Structure

### Documentation (this feature)

```text
specs/170-jira-ui-test-coverage/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jira-ui-test-coverage.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

api_service/api/routers/
├── jira_browser.py
└── task_dashboard_view_model.py

moonmind/integrations/jira/
└── browser.py

tests/unit/api/routers/
├── test_jira_browser.py
└── test_task_dashboard_view_model.py

tests/unit/integrations/
└── test_jira_browser_service.py
```

**Structure Decision**: Use existing runtime implementation surfaces and colocated unit tests. The Create page test file already owns task authoring, Jira browser, preset, step, dependency, and submission behavior. Backend validation belongs beside the Jira browser router, Jira browser service, and runtime config builder because those are the trusted boundaries this phase must protect.

## Phase 0: Research

See [research.md](./research.md).

Key decisions:

- Keep Phase 9 tests hermetic and fixture-driven; live Jira provider verification stays outside required CI.
- Grow existing focused test files instead of creating a parallel Jira Create-page harness.
- Treat production runtime edits as test-driven fixes only when a behavior gap is exposed.
- Preserve existing task submission payload shape and objective resolution as regression targets.

## Phase 1: Design

See:

- [data-model.md](./data-model.md)
- [contracts/jira-ui-test-coverage.yaml](./contracts/jira-ui-test-coverage.yaml)
- [contracts/requirements-traceability.md](./contracts/requirements-traceability.md)
- [quickstart.md](./quickstart.md)

## Post-Design Constitution Check

- **Runtime intent**: PASS. Design artifacts require production code fixes for discovered behavior gaps and validation tests for all requested surfaces.
- **Security boundary**: PASS. Browser-visible Jira paths, policy denial, and secret-safe errors are covered.
- **Failure isolation**: PASS. Tests must prove Jira failures remain local and manual task creation continues.
- **Submission compatibility**: PASS. Tests must prove Jira does not add a task type, create endpoint, or required payload fields.
- **DOC-REQ traceability**: PASS. `contracts/requirements-traceability.md` maps every DOC-REQ ID to FR IDs, planned surfaces, and validation strategy.

## Complexity Tracking

No Constitution violations. No complexity exceptions are required.
