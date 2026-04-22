# Implementation Plan: Mission Control Accessibility, Performance, and Fallback Posture

**Branch**: `mm-429-cbbc7b30` | **Date**: 2026-04-22 | **Spec**: `specs/223-accessibility-performance-fallbacks/spec.md`  
**Input**: Single-story runtime spec from trusted MM-429 Jira preset brief.

## Summary

Implement and verify the MM-429 accessibility, reduced-motion, fallback, and performance posture for Mission Control. Existing MM-425/MM-427/MM-428 work already provides shared surface tokens, focus rings, backdrop-filter fallback, liquidGL opt-in behavior, and route composition foundations. Planned work is test-first: add explicit Vitest coverage for MM-429 contrast/focus/fallback/reduced-motion/performance contracts, then make the smallest Mission Control CSS adjustments needed for any uncovered gaps while preserving existing task workflows.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/styles/mission-control.css` defines readable ink/panel/control tokens and existing visual-token tests cover selected surfaces | add explicit MM-429 contrast-bearing selector coverage; adjust CSS if any class lacks readable token usage | unit + integration-style UI |
| FR-002 | implemented_unverified | focus-visible rules exist for inputs, buttons, task controls, table sort, live log links, attachment controls, and task-detail toggles | add representative all-interactive-surface focus contract test; add missing focus selectors if exposed | unit + integration-style UI |
| FR-003 | partial | `@media (prefers-reduced-motion: reduce)` suppresses routine controls, but running step pulse and other live-effect selectors need explicit MM-429 coverage | add red-first reduced-motion test for pulse/live effects and extend CSS suppression if needed | unit |
| FR-004 | implemented_unverified | `@supports not ((backdrop-filter...))` fallback exists for glass controls, liquid hero, and floating bar | add explicit fallback contract test for glass and floating surfaces; adjust fallback selectors if gaps appear | unit |
| FR-005 | implemented_unverified | liquidGL hook fails safely and `.queue-floating-bar--liquid-glass` keeps CSS shell present before initialization | add contract coverage for liquidGL disabled/unavailable CSS shell and existing hook behavior | unit + integration-style UI |
| FR-006 | implemented_unverified | liquidGL is opt-in and tests assert it is absent from default dense surfaces | add MM-429 premium-effect limit assertions for dense/evidence/table/editing selectors | unit |
| FR-007 | implemented_verified | MM-428 task detail/evidence and dense-surface work keeps evidence/log/form regions matte/readable | preserve with regression tests | unit + integration-style UI |
| FR-008 | partial | quieter reduced-motion posture exists for controls only | extend quiet-mode CSS/tests to live pulses and nonessential visual effects | unit |
| FR-009 | implemented_unverified | existing task-list/create/detail tests cover behavior | run targeted UI regression after CSS changes | unit + integration-style UI |
| FR-010 | missing | no MM-429-specific test names/evidence yet | add focused tests before implementation | unit |
| FR-011 | implemented_verified | `spec.md` preserves MM-429 brief and source IDs | preserve through tasks and verification | final verify |
| DESIGN-REQ-003 | implemented_unverified | backdrop-filter fallback and CSS shell exist | add explicit MM-429 fallback tests | unit |
| DESIGN-REQ-006 | partial | routine control reduced-motion suppression exists, running pulse suppression gap remains | extend reduced-motion coverage and CSS | unit |
| DESIGN-REQ-015 | implemented_unverified | token-based contrast exists but needs explicit requirement coverage | add contrast selector tests | unit |
| DESIGN-REQ-022 | implemented_unverified | focus and fallback rules exist but need broader coverage | add focus/fallback tests | unit + integration-style UI |
| DESIGN-REQ-023 | implemented_unverified | liquidGL opt-in/dense-surface tests exist | add premium-effect limit tests and preserve dense matte posture | unit |

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
