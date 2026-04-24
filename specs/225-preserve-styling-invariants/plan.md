# Implementation Plan: Mission Control Styling Source and Build Invariants

**Branch**: `225-preserve-styling-invariants` | **Date**: 2026-04-22 | **Spec**: `specs/225-preserve-styling-invariants/spec.md`
**Input**: Single-story runtime spec from trusted MM-430 Jira preset brief.

## Summary

Implement and verify the MM-430 styling source and build invariants for Mission Control. Existing MM-425/MM-429 work already provides shared semantic classes, tokenized CSS, focus and fallback tests, and Tailwind source scanning. Planned work is test-first: add explicit MM-430 Vitest coverage for semantic class stability, additive modifiers, token-first role styling, light/dark token parity, Tailwind scan paths, and generated-asset boundaries, then make the smallest source-only CSS/config updates needed if those tests expose gaps.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | MM-430 semantic shell stability test in `frontend/src/entrypoints/mission-control.test.tsx`; template classes in `api_service/templates/react_dashboard.html` and `api_service/templates/_navigation.html`; CSS selectors in `frontend/src/styles/mission-control.css` | completed | unit + integration-style UI |
| FR-002 | implemented_verified | MM-430 additive modifier test in `frontend/src/entrypoints/mission-control.test.tsx` covers `panel--controls`, `panel--data`, `panel--floating`, `panel--utility`, and data-wide variants | completed | unit |
| FR-003 | implemented_verified | MM-430 token-first semantic role test in `frontend/src/entrypoints/mission-control.test.tsx` verifies representative role declarations use `--mm-*` tokens while allowing non-role visual effects | completed | unit |
| FR-004 | implemented_verified | MM-430 light/dark token-swap test in `frontend/src/entrypoints/mission-control.test.tsx` verifies token parity for shared surfaces | completed | unit |
| FR-005 | implemented_verified | MM-430 Tailwind source scanning test in `frontend/src/vite-config.test.ts`; required paths in `tailwind.config.cjs` | completed | unit |
| FR-006 | implemented_verified | MM-430 source-boundary test in `frontend/src/vite-config.test.ts` verifies canonical stylesheet path | completed | unit |
| FR-007 | implemented_verified | MM-430 generated dist boundary test in `frontend/src/vite-config.test.ts`; git status shows no changes under `api_service/static/task_dashboard/dist/` | completed | unit |
| FR-008 | implemented_verified | MM-430-specific tests exist in `mission-control.test.tsx` and `vite-config.test.ts` for all invariant groups | completed | unit |
| FR-009 | implemented_verified | Focused route regression suite passed for `mission-control.test.tsx`, `task-create.test.tsx`, `task-detail.test.tsx`, `tasks-list.test.tsx`, and `vite-config.test.ts` | completed | unit + integration-style UI |
| FR-010 | implemented_verified | `spec.md`, `tasks.md`, `verification.md`, and Jira orchestration input preserve MM-430 | completed | final verify |
| DESIGN-REQ-001 | implemented_verified | MM-430 semantic/token/theme tests plus route regressions preserve Mission Control operational styling identity | completed | unit + integration-style UI |
| DESIGN-REQ-024 | implemented_verified | Semantic shell and additive modifier tests in `mission-control.test.tsx` | completed | unit |
| DESIGN-REQ-025 | implemented_verified | Token-first, light/dark token parity, and Tailwind scan tests in `mission-control.test.tsx` and `vite-config.test.ts` | completed | unit |
| DESIGN-REQ-026 | implemented_verified | Canonical source/generated dist boundary tests in `vite-config.test.ts`; no dist files changed | completed | unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control tests; CSS for shared styling; Python 3.12 remains present but is not expected for this story.
**Primary Dependencies**: React, Vitest, Testing Library, PostCSS CSS inspection helpers, Tailwind config, existing Mission Control stylesheet.
**Storage**: No new persistent storage.
**Unit Testing**: Vitest via `npm run ui:test -- <paths>` or `./tools/test_unit.sh --ui-args <paths>`.
**Integration Testing**: Rendered React entrypoint tests act as integration-style coverage for Mission Control UI behavior; no compose-backed integration is required because backend contracts and persistence are unchanged.
**Target Platform**: Browser Mission Control UI.
**Project Type**: Frontend runtime behavior, CSS/build configuration invariants, and UI regression tests.
**Performance Goals**: No additional network calls; tests must not require rebuilding generated dist assets.
**Constraints**: Preserve task-list, task-creation, navigation, filtering, pagination, detail/evidence behavior, task submission payloads, backend contracts, Temporal contracts, Jira Orchestrate behavior, and generated dist boundaries.
**Scale/Scope**: One story across Mission Control shared chrome, route templates, stylesheet invariants, Tailwind scan configuration, and generated build-output boundaries.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The story changes Mission Control styling verification only and does not alter agent/provider orchestration.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisite changes.
- **III. Avoid Vendor Lock-In**: PASS. No provider-specific behavior is introduced.
- **IV. Own Your Data**: PASS. Data remains in existing task APIs and artifacts.
- **V. Skills Are First-Class**: PASS. No skill runtime change.
- **VI. Replaceable Scaffolding / Tests Anchor**: PASS. Focused tests define the styling/build invariant contract before any source changes.
- **VII. Runtime Configurability**: PASS. No runtime configuration semantics change.
- **VIII. Modular Architecture**: PASS. Work stays in shared Mission Control CSS/config tests and existing entrypoint tests.
- **IX. Resilient by Default**: PASS. The story protects source/build boundaries and avoids generated artifact drift.
- **X. Continuous Improvement**: PASS. Verification evidence will record outcome and remaining gaps.
- **XI. Spec-Driven Development**: PASS. Runtime work proceeds from this one-story spec, plan, and tasks.
- **XII. Canonical Documentation Separation**: PASS. Migration/run artifacts remain under `local-only handoffs` and `specs/`.
- **XIII. Pre-release Compatibility Policy**: PASS. No internal compatibility aliases or translation layers are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/225-preserve-styling-invariants/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│ └── styling-invariants.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
tailwind.config.cjs
api_service/templates/react_dashboard.html
api_service/templates/_navigation.html
api_service/static/task_dashboard/dist/
frontend/src/styles/mission-control.css
frontend/src/entrypoints/mission-control.test.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/entrypoints/task-detail.test.tsx
frontend/src/entrypoints/tasks-list.test.tsx
```

**Structure Decision**: Implement the story in existing Mission Control frontend tests and source styling/config files. Prefer CSS/config contract tests in `mission-control.test.tsx`; avoid backend, Temporal, Jira, and generated dist changes.

## Complexity Tracking

No constitution violations or extra architectural complexity are required.
