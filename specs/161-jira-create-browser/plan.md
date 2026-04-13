# Implementation Plan: Jira Create Browser

**Branch**: `161-jira-create-browser` | **Date**: 2026-04-12 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/161-jira-create-browser/spec.md`  
**Mode**: Runtime implementation. Deliverables must include production Create page behavior changes and validation tests, not docs-only changes.

## Summary

Add the Phase 4 Jira browser shell to the Create page as a runtime-config-gated instruction-source surface. The implementation extends the existing Create page runtime config consumption, state, React Query data loading, and form UI so operators can open one shared `Browse Jira story` browser from preset instructions or any step instructions, navigate project -> board -> column -> issue detail, and preview normalized Jira story content without importing text or changing task submission semantics.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for existing runtime config tests and final unit verification  
**Primary Dependencies**: React, TanStack Query, Vite/Vitest, FastAPI runtime boot payload, existing dashboard runtime config builder  
**Storage**: N/A for Phase 4; Jira browser state is transient Create page state only  
**Testing**: Vitest for focused Create page behavior, TypeScript typecheck, ESLint, `./tools/test_unit.sh` for final unit verification  
**Target Platform**: Browser-hosted Mission Control Create page served by MoonMind API service  
**Project Type**: Web application with API-provided boot/runtime configuration  
**Performance Goals**: Jira browsing fetches only when the browser is open and required selections exist; manual Create page editing remains responsive when Jira is disabled or unavailable  
**Constraints**: Browser clients use only MoonMind-owned Jira endpoints from runtime config; no browser-to-Jira credentials; no text import or task payload mutation in this phase; Jira failures remain local to the browser  
**Scale/Scope**: One Create page entrypoint, one shared browser surface, runtime-config-gated controls, focused frontend validation coverage plus existing runtime config tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan uses MoonMind's existing Jira browser/config boundaries and does not create a new Jira execution substrate.
- **II. One-Click Agent Deployment**: PASS. Jira UI remains disabled unless configured and does not add required external setup to baseline startup.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior is isolated behind MoonMind-owned browser endpoints and runtime config, not direct Jira browser calls.
- **IV. Own Your Data**: PASS. Jira content is fetched through MoonMind-controlled APIs and copied into local UI state for preview only in this phase.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime behavior changes are introduced.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. The browser consumes documented normalized contracts and keeps volatile Jira details server-side.
- **VII. Runtime Configurability**: PASS. Visibility and endpoints are controlled by runtime config and existing feature flags.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay in the Create page entrypoint, shared CSS, and focused tests.
- **IX. Resilient by Default**: PASS. Jira failures are local and do not block manual task creation.
- **X. Facilitate Continuous Improvement**: PASS. Tests cover the primary browser workflow and failure isolation.
- **XI. Spec-Driven Development**: PASS. This plan follows the generated spec and maps source document requirements.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are rewritten for migration status.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No compatibility aliases or legacy fallback contracts are added.
- **Security / Secret Hygiene**: PASS. Browser receives only MoonMind endpoint paths and normalized issue data; no secrets are exposed.
- **Validation Required**: PASS. The plan includes Vitest coverage, typecheck, lint, and unit-wrapper verification.

## Project Structure

### Documentation (this feature)

```text
specs/161-jira-create-browser/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jira-browser-openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

frontend/src/styles/
└── mission-control.css

api_service/api/routers/
└── task_dashboard_view_model.py

tests/unit/api/routers/
└── test_task_dashboard_view_model.py
```

**Structure Decision**: Use the existing Create page entrypoint and test file because the feature extends current task authoring surfaces rather than introducing a new page or task model. Reuse `task_dashboard_view_model.py` runtime config as the source of Jira endpoint discovery and feature gating.

## Phase 0: Research

See [research.md](research.md).

Key decisions:

- Use runtime config as the only browser discovery path for Jira UI capability and endpoint templates.
- Keep Jira browser data loading in the Create page entrypoint initially, using existing React Query patterns.
- Implement one shared modal/drawer-style browser surface instead of embedding separate browsers in every field.
- Treat text import, provenance persistence, session memory, and preset reapply UX as later phases.

## Phase 1: Design

See:

- [data-model.md](data-model.md)
- [contracts/jira-browser-openapi.yaml](contracts/jira-browser-openapi.yaml)
- [contracts/requirements-traceability.md](contracts/requirements-traceability.md)
- [quickstart.md](quickstart.md)

## Post-Design Constitution Check

- **Runtime Configurability**: PASS. Jira UI remains gated by `system.jiraIntegration.enabled` and `sources.jira`.
- **Security / Secret Hygiene**: PASS. The browser uses only MoonMind-owned paths; no Jira credentials or external Jira URLs are embedded for browser calls.
- **Resiliency**: PASS. Loading and error states are local to the browser and manual task creation remains available.
- **Spec Traceability**: PASS. Every `DOC-REQ-*` has mapped implementation surfaces and validation strategy in `contracts/requirements-traceability.md`.
- **Validation**: PASS. The plan includes focused Create page tests and repo-level unit verification.

## Complexity Tracking

No Constitution violations. No additional complexity exceptions are required.
