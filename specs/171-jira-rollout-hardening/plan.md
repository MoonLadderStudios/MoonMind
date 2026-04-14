# Implementation Plan: Jira Create-Page Rollout Hardening

**Branch**: `171-jira-rollout-hardening` | **Date**: 2026-04-14 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/171-jira-rollout-hardening/spec.md`

## Summary

Complete and harden the Jira Create-page rollout as a runtime feature, not a docs-only change. The implementation keeps Jira additive to manual task creation by using MoonMind-owned browser endpoints, runtime-config-gated UI entry points, one shared Create-page Jira browser, explicit import actions for preset and step targets, visible preset reapply/provenance feedback, and validation tests across backend, runtime config, and frontend behavior.

## Technical Context

**Language/Version**: Python 3.11+ backend, TypeScript/React frontend  
**Primary Dependencies**: FastAPI, Pydantic, httpx, React, TanStack Query, Vite/Vitest, pytest  
**Storage**: No new durable storage; Jira provenance remains local Create-page UI state and optional browser-session preference  
**Testing**: `./tools/test_unit.sh` for Python and dashboard unit tests, targeted `pytest`, targeted Vitest via `node_modules/.bin/vitest`, TypeScript typecheck, ESLint  
**Target Platform**: MoonMind API service and Mission Control web UI in the existing Docker Compose deployment  
**Project Type**: Web application with Python API service, shared MoonMind integration layer, and React dashboard frontend  
**Performance Goals**: Jira browser interactions should remain responsive for ordinary Jira boards; issue list pagination should be bounded and failures should return promptly through existing Jira timeout/retry settings  
**Constraints**: Browser clients must only call MoonMind endpoints; Jira credentials and SecretRefs stay server-side; Jira UI remains hidden unless explicitly enabled; manual task creation must remain available when Jira is disabled or failing; task submission payload shape remains unchanged  
**Scale/Scope**: One Create-page Jira browser MVP covering connection verification, projects, boards, columns, issue lists, issue detail, import into preset/step fields, reapply/provenance UX, and focused validation tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Jira remains an external instruction source; the feature does not create a new agent runtime or cognition layer.
- **II. One-Click Agent Deployment**: PASS. Jira UI is disabled by default and uses safe no-op behavior without requiring Jira secrets for baseline startup.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior remains isolated in the Jira integration/browser boundary and is not coupled into task submission semantics.
- **IV. Own Your Data**: PASS. Imported Jira text is copied into operator-controlled task draft state; browser clients do not receive credentials.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature does not change executable skill contracts or active skill-set resolution.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. The rollout relies on explicit read contracts, tests, and isolated UI state rather than broad Create-page rewrites.
- **VII. Powerful Runtime Configurability**: PASS. UI exposure is controlled by namespaced runtime configuration and boot payload flags.
- **VIII. Modular and Extensible Architecture**: PASS. Backend browser reads, runtime config, and frontend browser state remain separate surfaces with clear contracts.
- **IX. Resilient by Default**: PASS. Jira failures are localized and do not block manual task creation; provider errors are structured and redacted.
- **X. Facilitate Continuous Improvement**: PASS. Validation tests and explicit failure states make future Jira improvements auditable.
- **XI. Spec-Driven Development**: PASS. This plan follows `spec.md`; implementation must update spec/plan/tasks if scope changes.
- **XII. Canonical Documentation Separation**: PASS. Runtime implementation planning stays under `specs/`; canonical docs need only target-state updates if behavior changes.
- **XIII. Pre-Release Velocity**: PASS. No compatibility aliases or fallback contract shims are planned for internal Create-page/Jira contracts.

## Project Structure

### Documentation (this feature)

```text
specs/171-jira-rollout-hardening/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── jira-browser.openapi.yaml
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── jira_browser.py
│   └── task_dashboard_view_model.py
└── main.py

moonmind/
├── config/settings.py
└── integrations/jira/
    ├── auth.py
    ├── browser.py
    ├── client.py
    └── errors.py

frontend/src/
└── entrypoints/
    ├── task-create.tsx
    └── task-create.test.tsx

tests/
└── unit/
    ├── api/routers/
    │   ├── test_jira_browser.py
    │   └── test_task_dashboard_view_model.py
    ├── config/test_settings.py
    └── integrations/
        ├── test_jira_browser_service.py
        └── test_jira_client.py
```

**Structure Decision**: Use the existing Mission Control/Create-page structure. Runtime config remains in the dashboard view-model helper; trusted Jira browser reads remain in the Jira integration service and API router; UI state and import behavior remain in the existing Create-page entrypoint until further extraction is justified by real complexity.

## Phase 0: Research

See [research.md](./research.md). Key decisions:

- Reuse trusted server-side Jira auth/client boundaries for all browser reads.
- Publish Jira UI capability only through Create-page runtime config.
- Keep Jira provenance local and out of task submission for the MVP.
- Validate failure handling as an additive UI capability, not a task submission dependency.

## Phase 1: Design & Contracts

Design artifacts:

- [data-model.md](./data-model.md): Create-page Jira config, browse entities, import target/provenance state, validation rules.
- [contracts/jira-browser.openapi.yaml](./contracts/jira-browser.openapi.yaml): Browser-facing read contracts exposed under MoonMind-owned Jira routes.
- [quickstart.md](./quickstart.md): deterministic validation path for runtime config, backend service/router behavior, frontend import behavior, typecheck, and lint.

## Post-Design Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Design remains a read/import UI feature.
- **II. One-Click Agent Deployment**: PASS. Jira Create-page controls stay disabled by default and baseline startup remains local-first.
- **III. Avoid Vendor Lock-In**: PASS. Vendor-specific Jira normalization is contained in the Jira integration boundary.
- **IV. Own Your Data**: PASS. No browser credentials; copied story text stays in MoonMind task draft state.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime mutation.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. OpenAPI contract and tests anchor behavior.
- **VII. Powerful Runtime Configurability**: PASS. Feature flag and defaults are operator-controlled.
- **VIII. Modular and Extensible Architecture**: PASS. Router, service, runtime config, and UI remain modular.
- **IX. Resilient by Default**: PASS. Failure states are structured, redacted, and local to Jira browser flows.
- **X. Facilitate Continuous Improvement**: PASS. Quickstart and tests provide repeatable verification.
- **XI. Spec-Driven Development**: PASS. Generated artifacts trace to spec requirements and success criteria.
- **XII. Canonical Documentation Separation**: PASS. Implementation plan lives in spec artifacts, not canonical docs.
- **XIII. Pre-Release Velocity**: PASS. No legacy aliases or compatibility layers introduced.

## Complexity Tracking

No constitution violations or justified complexity exceptions.
