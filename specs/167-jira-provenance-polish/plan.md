# Implementation Plan: Jira Provenance Polish

**Branch**: `167-jira-provenance-polish` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `specs/167-jira-provenance-polish/spec.md`

## Summary

Add Create page runtime polish for Jira imports by tracking local per-target provenance, rendering compact Jira issue chips next to edited preset or step instruction fields, remembering the last Jira project and board only in the current browser session when enabled by runtime config, and preserving the existing task submission contract. The implementation stays in the existing Create page UI boundary and expands frontend validation around the established Jira browser/import test fixture.

## Technical Context

**Language/Version**: TypeScript/React frontend in the existing Vite Mission Control bundle; Python 3.12 backend remains unchanged for this phase.  
**Primary Dependencies**: Existing React state, TanStack Query-powered Jira browser data flow, Create page preset/step state, Vitest, Testing Library, TypeScript compiler.  
**Storage**: Client-side Create page state for provenance; browser session storage for optional last project/board memory. No backend storage and no task payload persistence for Jira provenance.  
**Testing**: Focused Vitest/Testing Library coverage for `frontend/src/entrypoints/task-create.test.tsx`, TypeScript typecheck, and the repo unit wrapper for dashboard/full unit validation.  
**Target Platform**: Mission Control Create page served by FastAPI and rendered in modern browsers.  
**Project Type**: Web application frontend change inside the existing MoonMind monorepo.  
**Performance Goals**: Provenance rendering and session-memory selection must be synchronous UI state updates with no additional network calls beyond existing Jira browser endpoint requests.  
**Constraints**: Runtime mode; deliverables must include production Create page code and validation tests. Jira remains optional, feature-gated, and additive. Browser clients must continue using MoonMind-owned Jira data only. Task submission payload shape must remain unchanged. Manual task creation must remain available if Jira fails or browser session storage is unavailable.  
**Scale/Scope**: One shared Jira browser surface, one selected import target at a time, one preset provenance record, and one provenance record per active step.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature adjusts task authoring UI state and does not introduce or replace agent behavior.
- **II. One-Click Agent Deployment**: PASS. Jira remains optional and runtime-gated; no new required service, secret, or deployment prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Jira remains an optional instruction source copied into MoonMind-native draft fields; provenance is local metadata, not an execution dependency.
- **IV. Own Your Data**: PASS. Imported text remains in MoonMind task fields, and local provenance/session memory stays under the operator's browser/session control.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill contract or runtime skill behavior changes.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. The plan uses existing UI boundaries and tests the behavior contract rather than adding broad abstractions.
- **VII. Powerful Runtime Configurability**: PASS. Session memory is controlled by the existing Jira integration runtime config flag.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to the Create page entrypoint, its tests, and styling; no cross-module integration rewrite.
- **IX. Resilient by Default**: PASS. Jira and session-storage failures remain local and must not block manual authoring or task creation.
- **X. Facilitate Continuous Improvement**: PASS. Validation catches provenance/session regressions before merge.
- **XI. Spec-Driven Development**: PASS. This plan is derived from `spec.md` and will feed task generation.
- **XII. Canonical Documentation Separation**: PASS. No canonical docs are modified for migration tracking; implementation planning remains under `specs/`.
- **XIII. Pre-Release Velocity**: PASS. No compatibility aliases or deprecated duplicate paths are planned.
- **Product/Operational Constraints**: PASS. No secrets are handled, no workflow contract changes are introduced, and Mission Control remains the operator-facing surface.

## Project Structure

### Documentation (this feature)

```text
specs/167-jira-provenance-polish/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-jira-provenance.yaml
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/
├── entrypoints/
│   ├── task-create.tsx
│   └── task-create.test.tsx
└── styles/
    └── mission-control.css
```

**Structure Decision**: Implement runtime behavior in `frontend/src/entrypoints/task-create.tsx` because that entrypoint already owns Jira browser open/close state, import target state, preset instructions, step updates, and task submission assembly. Add user-visible chip styling in `frontend/src/styles/mission-control.css`. Validate through `frontend/src/entrypoints/task-create.test.tsx`, reusing existing Jira browser mocks and Create page submission assertions. No backend router, runtime config builder, or task execution code changes are planned for this phase.

## Phase 0: Research

Research decisions are captured in [research.md](./research.md). Key outcomes:

- Track provenance in local Create page state and clear it on manual edits.
- Store last Jira project/board in session storage only when runtime config enables it.
- Keep Jira provenance out of task submission payloads for the MVP.
- Validate with focused Create page tests plus typecheck/unit wrapper verification.

## Phase 1: Design & Contracts

Design artifacts:

- [data-model.md](./data-model.md) defines `JiraImportProvenance`, `JiraImportTarget`, and `SessionJiraSelection`.
- [contracts/create-page-jira-provenance.yaml](./contracts/create-page-jira-provenance.yaml) defines the Create page UI state/action contract for provenance and session-memory behavior.
- [quickstart.md](./quickstart.md) describes deterministic validation steps.

There are no `DOC-REQ-*` identifiers in `spec.md`; no `requirements-traceability.md` artifact is required.

## Constitution Check

*Post-design re-check.*

- **Runtime intent**: PASS. The design requires production Create page behavior and validation tests.
- **Runtime configurability**: PASS. Session memory is active only when the existing runtime config flag enables it.
- **Submission boundary**: PASS. The design keeps Jira provenance out of submitted task payloads.
- **Failure isolation**: PASS. Session-storage failures and Jira browser failures remain local and do not block manual task creation.
- **Security/secret hygiene**: PASS. The feature does not expose credentials or add browser-to-Jira calls.

## Complexity Tracking

No constitution violations or complexity exceptions are required.
