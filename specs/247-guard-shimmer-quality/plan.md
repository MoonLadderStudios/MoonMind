# Implementation Plan: Shimmer Quality Regression Guardrails

**Branch**: `247-guard-shimmer-quality` | **Date**: 2026-04-24 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/247-guard-shimmer-quality/spec.md`

## Summary

MM-491 is the regression-guardrail story that follows the shared shimmer host contract from MM-488 and the visual/motion refinements from MM-489 and MM-490. The planned work stays frontend-only and verification-first: add focused CSS/helper and render tests that prove executing-state readability, bounded clipping, no layout shift, state-matrix isolation, theme intent, reduced-motion fallback clarity, and MM-491 traceability; only if those tests expose a real gap should the shared Mission Control shimmer contract or runtime-adjacent traceability surface be adjusted.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/styles/mission-control.css` already keeps the executing shimmer clipped with `overflow: hidden`, and `frontend/src/entrypoints/mission-control.test.tsx` asserts shared selector/timing tokens, but no current MM-491 test proves executing-label readability together with bounds/scrollbar safety. | add focused CSS contract assertions and render verification for readability, clipping, and scrollbar isolation; adjust shared shimmer styling only if those tests fail | unit + integration |
| FR-002 | implemented_unverified | `frontend/src/utils/executionStatusPillClasses.ts` limits shimmer metadata to `executing`, and current helper/list/detail tests prove selected non-executing states stay plain, but the full source state matrix is not covered as one MM-491 regression set. | expand helper/render tests to cover the full listed non-executing state matrix and keep activation executing-only | unit + integration |
| FR-003 | partial | The shared shimmer contract already renders as a background overlay inside the pill, but no existing test demonstrates pill dimensions and surrounding layout remain unchanged before, during, and after activation. | add layout-stability assertions first; update shared CSS or pill markup only if the new tests expose a footprint regression | unit + integration |
| FR-004 | implemented_unverified | `frontend/src/entrypoints/mission-control.test.tsx` proves light/dark token swaps and shared shimmer tokens exist, but no story-level evidence yet shows the executing shimmer reads as an intentional active treatment in both themes. | add theme-aware CSS and/or render assertions for executing active treatment in light and dark themes; make minimal style adjustments only if required by failing tests | unit + integration |
| FR-005 | implemented_unverified | `frontend/src/styles/mission-control.css` disables shimmer animation under reduced motion and keeps a static highlight, while list/detail render tests already exercise executing pills, but the static fallback is not yet verified as an explicit MM-491 active cue. | add focused reduced-motion verification across shared CSS and supported surfaces; adjust fallback emphasis only if tests reveal ambiguity | unit + integration |
| FR-006 | implemented_unverified | The source design non-goals are preserved in spec artifacts and adjacent shimmer stories, but there is no dedicated MM-491 proof that the story is satisfied through regression guardrails rather than by swapping in an alternate effect family. | encode non-goal expectations through shared shimmer contract tests and verification notes; implementation changes only if tests reveal drift away from the existing shimmer model | unit + integration |
| FR-007 | missing | `frontend/src/utils/executionStatusPillClasses.ts` currently preserves `MM-488` as the primary traceability issue and `MM-489`/`MM-490` as related issues, with no MM-491 runtime-adjacent reference yet. | extend the traceability surface and adjacent tests to preserve MM-491 without dropping neighboring shimmer-story references | unit |
| SCN-001 | implemented_unverified | Shared CSS tokens and selector tests prove the executing shimmer exists, but no MM-491 test yet joins readability, bounds, and scrollbar isolation into one scenario. | add CSS and render verification first, then implementation contingency if exposed gaps appear | unit + integration |
| SCN-002 | implemented_unverified | Existing helper/list/detail tests show selected non-executing states remain plain, but not every state from the design matrix is covered together. | add state-matrix regression coverage for non-executing states | unit + integration |
| SCN-003 | partial | No current assertion compares layout before and after shimmer activation. | add layout-stability tests first; patch shared CSS/markup only if needed | unit + integration |
| SCN-004 | implemented_unverified | Theme token coverage exists, but executing-specific theme treatment is not directly proven. | add theme-aware shimmer assertions | unit + integration |
| SCN-005 | implemented_unverified | Reduced-motion suppression exists and supported surfaces render executing pills, but MM-491-specific active-fallback semantics are not fully proven. | add reduced-motion fallback assertions first; patch styling only if necessary | unit + integration |
| SC-001 | implemented_unverified | Readability is implied by current shared text-over-effect layering, but sampled-point readability is not directly tested. | add CSS/render evidence for readability at sampled sweep points | unit + integration |
| SC-002 | implemented_unverified | Clipping evidence exists through shared CSS plus `overflow: hidden`, but scrollbar non-interaction is not directly asserted for the shimmer contract. | add CSS contract checks for clipping and scrollbar isolation | unit + integration |
| SC-003 | implemented_unverified | Executing-only behavior is covered for selected cases, not the full non-executing state set. | expand state-matrix tests | unit + integration |
| SC-004 | partial | Layout-stability proof is missing today. | add layout assertions first; implementation contingency if tests fail | unit + integration |
| SC-005 | implemented_unverified | Light/dark token swaps are covered, but intentional executing treatment in both themes is not directly verified. | add theme-specific active-treatment assertions | unit + integration |
| SC-006 | implemented_unverified | Reduced-motion disablement exists, but the static active fallback is not yet proven end to end for MM-491. | add reduced-motion active-fallback coverage | unit + integration |
| SC-007 | partial | MM-491 appears in spec artifacts, but not yet in runtime-adjacent traceability evidence. | add MM-491 traceability to helper/test evidence and preserve it through final verification | unit |
| DESIGN-REQ-004 | implemented_unverified | Host text preservation and non-layout-changing intent are reflected in the current selector contract, but MM-491-specific readability/layout guardrail tests are missing. | add readability and layout assertions first | unit + integration |
| DESIGN-REQ-009 | implemented_unverified | Existing CSS encodes `overflow: hidden`, `isolation: isolate`, and non-pointer-intercepting background treatment, but MM-491 evidence for bounds, text priority, and scrollbar isolation is incomplete. | add CSS contract assertions and surface verification | unit + integration |
| DESIGN-REQ-011 | implemented_unverified | Reduced-motion disablement exists, but the preserved active fallback is not yet proven as a story-level regression guardrail. | add reduced-motion fallback assertions | unit + integration |
| DESIGN-REQ-014 | implemented_unverified | Executing-only routing exists today, but the listed non-executing state matrix is not fully covered in one MM-491 story. | add full state-matrix regression coverage | unit + integration |
| DESIGN-REQ-016 | implemented_unverified | Adjacent shimmer stories preserve non-goal boundaries, but MM-491 lacks direct verification that the existing shimmer model remains the object under test. | add contract and verification evidence that preserves the intended shimmer model and rejects substitute effect families | unit + integration |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, shared execution-status helper, existing task list/detail render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Focused Vitest CSS/helper coverage via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` plus final `./tools/test_unit.sh`  
**Integration Testing**: Frontend entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed `integration_ci` suite is expected for this isolated UI behavior  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet, helper traceability, and supported status-pill render-surface verification  
**Performance Goals**: Preserve label readability, keep the shimmer bounded to the current pill footprint, avoid layout shift, and keep the regression suite focused on the shared CSS/helper contract rather than introducing page-local forks  
**Constraints**: Runtime mode only; preserve shared shimmer reuse; keep the story scoped to regression guardrails; preserve MM-491 traceability; keep unit and integration strategies explicit; managed branch naming does not satisfy the setup-plan helper convention  
**Scale/Scope**: Shared Mission Control shimmer contract plus list/detail status-pill surfaces and focused frontend tests only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup dependency changes.
- III. Avoid Vendor Lock-In: PASS - UI regression guardrails remain provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the story is governed by shared CSS/helper contracts and focused tests rather than page-local logic.
- VII. Runtime Configurability: PASS - behavior remains driven by state selectors, shared tokens, and reduced-motion media queries.
- VIII. Modular and Extensible Architecture: PASS - work stays within existing Mission Control helper, stylesheet, and render-test seams.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - explicit requirement status, traceability, and verification evidence remain planned.
- XI. Spec-Driven Development: PASS - spec and planning artifacts exist before task generation.
- XII. Canonical Documentation Separation: PASS - planning artifacts remain under `specs/247-guard-shimmer-quality/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim is planned; the shared shimmer contract will be updated directly if failing tests expose a gap.

## Project Structure

### Documentation (this feature)

```text
specs/247-guard-shimmer-quality/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer-quality.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
└── spec.md
```

### Source Code (repository root)

```text
frontend/src/
├── entrypoints/
│   ├── mission-control.test.tsx
│   ├── task-detail.test.tsx
│   └── tasks-list.test.tsx
├── styles/
│   └── mission-control.css
└── utils/
    ├── executionStatusPillClasses.test.ts
    └── executionStatusPillClasses.ts
```

**Structure Decision**: Reuse the shared Mission Control status-pill helper, stylesheet, and existing task list/detail surfaces. Do not introduce a separate component or a page-local shimmer variant for MM-491.

## Complexity Tracking

No constitution violations or added architectural complexity.
