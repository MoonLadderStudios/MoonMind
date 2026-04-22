# Implementation Plan: Mission Control Accessibility, Performance, and Fallback Posture

**Branch**: `mm-429-cbbc7b30` | **Date**: 2026-04-22 | **Spec**: `specs/223-accessibility-performance-fallbacks/spec.md`  
**Input**: Single-story runtime spec from trusted MM-429 Jira preset brief.

## Summary

Implement and verify the MM-429 accessibility, reduced-motion, fallback, and performance posture for Mission Control. Existing MM-425/MM-427/MM-428 work already provides shared surface tokens, focus rings, backdrop-filter fallback, liquidGL opt-in behavior, and route composition foundations. Planned work is test-first: add explicit Vitest coverage for MM-429 contrast/focus/fallback/reduced-motion/performance contracts, then make the smallest Mission Control CSS adjustments needed for any uncovered gaps while preserving existing task workflows.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | MM-429 contrast contract tests in `frontend/src/entrypoints/mission-control.test.tsx`; readable token CSS in `frontend/src/styles/mission-control.css` | completed | unit + integration-style UI |
| FR-002 | implemented_verified | MM-429 focus-visible coverage tests in `frontend/src/entrypoints/mission-control.test.tsx`; focus CSS in `frontend/src/styles/mission-control.css` | completed | unit + integration-style UI |
| FR-003 | implemented_verified | MM-429 reduced-motion tests in `frontend/src/entrypoints/mission-control.test.tsx`; reduced-motion pulse and premium-surface CSS in `frontend/src/styles/mission-control.css` | completed | unit |
| FR-004 | implemented_verified | MM-429 fallback shell tests in `frontend/src/entrypoints/mission-control.test.tsx`; `@supports not` fallback CSS in `frontend/src/styles/mission-control.css` | completed | unit |
| FR-005 | implemented_verified | MM-429 liquidGL fallback tests in `frontend/src/entrypoints/task-create.test.tsx` and `frontend/src/lib/liquidGL/useLiquidGL.test.tsx`; CSS shell in `frontend/src/styles/mission-control.css` | completed | unit + integration-style UI |
| FR-006 | implemented_verified | MM-429 premium-effect containment tests in `frontend/src/entrypoints/mission-control.test.tsx` | completed | unit |
| FR-007 | implemented_verified | MM-428 task detail/evidence and dense-surface work plus MM-429 route regression validation | completed | unit + integration-style UI |
| FR-008 | implemented_verified | MM-429 reduced-motion tests and quiet-mode CSS in `frontend/src/styles/mission-control.css` | completed | unit |
| FR-009 | implemented_verified | Route regression tests for task list, create page, and task detail passed after CSS changes | completed | unit + integration-style UI |
| FR-010 | implemented_verified | MM-429-specific tests exist in `mission-control.test.tsx`, `task-create.test.tsx`, and `useLiquidGL.test.tsx` | completed | unit |
| FR-011 | implemented_verified | `spec.md`, `tasks.md`, and `verification.md` preserve MM-429 brief and source IDs | completed | final verify |
| DESIGN-REQ-003 | implemented_verified | Backdrop-filter and liquidGL fallback tests/CSS | completed | unit |
| DESIGN-REQ-006 | implemented_verified | Reduced-motion pulse and premium-surface tests/CSS | completed | unit |
| DESIGN-REQ-015 | implemented_verified | Contrast selector tests/CSS | completed | unit |
| DESIGN-REQ-022 | implemented_verified | Focus-visible and fallback tests/CSS | completed | unit + integration-style UI |
| DESIGN-REQ-023 | implemented_verified | Premium-effect containment tests/CSS | completed | unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; CSS for shared Mission Control styling; Python 3.12 remains present but is not expected for this story.  
**Primary Dependencies**: React, TanStack Query, existing Mission Control stylesheet, liquidGL enhancement wrapper, Vitest, Testing Library, PostCSS CSS inspection helpers.  
**Storage**: No new persistent storage.  
**Unit Testing**: Vitest via `npm run ui:test -- <paths>` or `./tools/test_unit.sh --ui-args <paths>`.  
**Integration Testing**: Rendered React entrypoint tests act as integration-style coverage for Mission Control UI behavior; no compose-backed integration is required because backend contracts and persistence are unchanged.  
**Target Platform**: Browser Mission Control UI.  
**Project Type**: Frontend runtime behavior, CSS resilience, and UI regression tests.  
**Performance Goals**: No additional network calls; heavy blur, glow, sticky glass, and liquidGL effects remain limited to strategic surfaces; fallback paths preserve usable rendering without advanced effects.  
**Constraints**: Preserve existing task-list, task-creation, navigation, filtering, pagination, detail/evidence behavior, task submission payloads, Jira Orchestrate behavior, and Temporal contracts.  
**Scale/Scope**: One story across Mission Control shared chrome, task workflow routes, dense content regions, liquidGL target surfaces, and reduced-motion/fallback modes.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The story changes Mission Control UI resilience only and does not alter agent/provider orchestration.
- **II. One-Click Agent Deployment**: PASS. No deployment or prerequisite changes.
- **III. Avoid Vendor Lock-In**: PASS. No provider-specific behavior is introduced.
- **IV. Own Your Data**: PASS. Data remains in existing task APIs and artifacts.
- **V. Skills Are First-Class**: PASS. No skill runtime change.
- **VI. Replaceable Scaffolding / Tests Anchor**: PASS. Focused tests define the UI resilience contract before CSS adjustments.
- **VII. Runtime Configurability**: PASS. Existing runtime behavior remains controlled by current UI/runtime settings; no hardcoded environment changes.
- **VIII. Modular Architecture**: PASS. Work stays in shared Mission Control CSS and focused entrypoint tests.
- **IX. Resilient by Default**: PASS. The story improves graceful degradation and quiet-mode behavior.
- **X. Continuous Improvement**: PASS. Verification evidence will record outcome and remaining gaps.
- **XI. Spec-Driven Development**: PASS. Runtime changes proceed from this one-story spec, plan, and tasks.
- **XII. Canonical Documentation Separation**: PASS. Migration/run artifacts remain under `docs/tmp` and `specs/`.
- **XIII. Pre-release Compatibility Policy**: PASS. No internal contract aliases or compatibility transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/223-accessibility-performance-fallbacks/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── accessibility-fallbacks.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/mission-control.test.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/entrypoints/task-detail.test.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/lib/liquidGL/useLiquidGL.test.tsx
frontend/src/styles/mission-control.css
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-detail.tsx
frontend/src/entrypoints/tasks-list.tsx
```

**Structure Decision**: Implement the story in the existing Mission Control frontend layer. Prefer CSS contract tests in `mission-control.test.tsx` and targeted route regression tests over backend or workflow changes.

## Complexity Tracking

No constitution violations or extra architectural complexity are required.
