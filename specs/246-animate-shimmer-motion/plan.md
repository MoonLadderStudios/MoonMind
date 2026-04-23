# Implementation Plan: Calm Shimmer Motion and Reduced-Motion Fallback

**Branch**: `246-animate-shimmer-motion` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/246-animate-shimmer-motion/spec.md`

## Summary

Plan MM-490 as the motion-profile refinement on top of the shared executing shimmer already introduced by MM-488 and visually refined by MM-489. The repo already contains an executing-only shimmer selector contract, shared Mission Control CSS, reduced-motion suppression, and list/detail render coverage, but the current implementation does not yet defensibly encode or prove the full MM-490 motion cadence: the repeat delay is only an initial `animation-delay`, center-brightness emphasis is indirect, and MM-490 traceability is absent from runtime-adjacent evidence. Planned work is frontend-only and TDD-first: add focused CSS/helper tests for cadence, no-overlap timing, center-brightness emphasis, reduced-motion active fallback, and MM-490 traceability; update the shared Mission Control shimmer contract where those tests expose gaps; then rerun focused Vitest coverage and the full unit suite.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/styles/mission-control.css` defines left-to-right shimmer positions plus `overflow: hidden`, but no MM-490-focused test proves bounded travel semantics. | add verification tests first, with implementation contingency if clipping/path assertions fail | unit + integration |
| FR-002 | partial | CSS defines `--mm-executing-sweep-duration: 1450ms` and `--mm-executing-sweep-delay: 220ms`, but the shimmer uses initial `animation-delay` rather than a per-cycle repeat gap and does not defensibly prove no-overlap cadence. | add failing tests, then adjust shared shimmer timing contract and keyframes | unit + integration |
| FR-003 | partial | Current keyframes reach `50%` / `62%` positions mid-cycle, but no explicit midpoint brightness contract or proof exists. | add failing tests, then refine shimmer intensity/timing behavior if needed | unit + integration |
| FR-004 | implemented_unverified | Reduced-motion CSS disables animation and keeps a fixed highlight frame, but story-level static-active semantics are not explicitly verified. | add verification tests first, with implementation contingency if fallback treatment is too weak | unit + integration |
| FR-005 | implemented_unverified | Existing reduced-motion CSS and executing pill renders imply active comprehension without motion, but no MM-490-specific evidence proves it. | add verification tests first, with implementation contingency if reduced-motion read is unclear | unit + integration |
| FR-006 | implemented_verified | `executionStatusPillProps` and existing list/detail tests keep shimmer metadata executing-only. | no new implementation planned | none beyond final verify |
| FR-007 | missing | Runtime-adjacent traceability currently preserves `MM-488` and `MM-489`, not `MM-490`. | add MM-490 traceability export/assertions plus focused tests | unit |
| SCN-001 | implemented_unverified | Existing CSS suggests bounded left-to-right sweep behavior, but story-specific proof is absent. | add verification tests first, with implementation contingency if bounds/path fail | unit + integration |
| SCN-002 | partial | Existing timing tokens and keyframes approximate the cadence but do not prove total cycle, idle gap, or no-overlap behavior. | add failing tests, then refine timing contract | unit + integration |
| SCN-003 | partial | Midpoint positioning exists, but brightest-at-center behavior is not explicitly encoded or tested. | add failing tests, then refine midpoint emphasis if needed | unit + integration |
| SCN-004 | implemented_unverified | Reduced-motion animation suppression exists, but the static active replacement is only indirectly verified. | add verification tests first, with implementation contingency if fallback is insufficient | unit + integration |
| SCN-005 | implemented_unverified | Existing reduced-motion output likely reads as active, but no direct MM-490 story evidence exists. | add verification tests first, with implementation contingency if comprehension is weak | unit + integration |
| SCN-006 | implemented_verified | Existing helper and render tests already keep shimmer off for non-executing states. | no new implementation planned | none beyond final verify |
| SC-001 | implemented_unverified | Current CSS implies left-to-right bounded travel but lacks direct MM-490 evidence. | add verification tests first, with implementation contingency if movement contract fails | unit + integration |
| SC-002 | partial | Current tokens do not fully prove 1.6-1.8 second cadence with no overlap between cycles. | add failing tests, then refine timing contract | unit + integration |
| SC-003 | partial | Center-brightness emphasis is implied by keyframe position only. | add failing tests, then refine midpoint emphasis | unit + integration |
| SC-004 | implemented_unverified | Reduced-motion suppression is tested, but static active fallback semantics are not fully proved. | add verification tests first, with implementation contingency if fallback is insufficient | unit + integration |
| SC-005 | implemented_unverified | Reduced-motion active comprehension is likely present but not directly verified. | add verification tests first, with implementation contingency if the treatment does not read as active | unit + integration |
| SC-006 | implemented_verified | Existing helper and entrypoint tests prove shimmer remains off for non-executing states. | no new implementation planned | none beyond final verify |
| SC-007 | missing | MM-490 appears in spec artifacts only, not in runtime-adjacent traceability evidence. | add MM-490 traceability export/assertions plus focused tests | unit |
| DESIGN-REQ-007 | implemented_unverified | Executing-state trigger and reduced-motion path exist in helper/CSS, but MM-490 story evidence is incomplete. | add verification tests first, with implementation contingency if selector or fallback semantics drift | unit + integration |
| DESIGN-REQ-010 | partial | Motion path and base timing tokens exist, but repeat-gap/no-overlap and midpoint-emphasis details are incomplete. | add failing tests, then refine keyframes/timing contract | unit + integration |
| DESIGN-REQ-012 | implemented_unverified | Reduced-motion animation suppression exists, but static-highlight semantics are not fully proved. | add verification tests first, with implementation contingency if fallback semantics fail | unit + integration |
| DESIGN-REQ-014 | implemented_verified | Shimmer activation remains executing-only through helper and render coverage. | no new implementation planned | none beyond final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, shared execution-status helper, existing entrypoint render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Focused Vitest CSS/helper coverage via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` plus final `./tools/test_unit.sh`  
**Integration Testing**: Frontend entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed `integration_ci` suite is expected for this isolated UI behavior  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet and status-pill rendering refinement  
**Performance Goals**: Preserve pill readability, keep the shimmer bounded to the existing pill footprint, avoid layout shift, and maintain lightweight CSS-only animation with an explicit reduced-motion fallback  
**Constraints**: Runtime mode only; preserve shared shimmer reuse; keep the story scoped to motion profile and reduced-motion fallback; no page-local animation forks; preserve MM-490 traceability; keep unit and integration strategies explicit; managed branch naming does not satisfy the setup-plan helper convention  
**Scale/Scope**: Shared Mission Control shimmer tokens/keyframes plus list/detail status-pill surfaces, with focused frontend tests only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup dependency changes.
- III. Avoid Vendor Lock-In: PASS - UI behavior remains provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the story is governed by shared CSS/helper contracts and focused tests rather than page-local logic.
- VII. Runtime Configurability: PASS - behavior remains driven by state selectors, shared tokens, and reduced-motion media queries.
- VIII. Modular and Extensible Architecture: PASS - work stays within existing Mission Control helper, stylesheet, and render-test seams.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - explicit requirement status, traceability, and verification evidence remain planned.
- XI. Spec-Driven Development: PASS - spec and planning artifacts exist before task generation.
- XII. Canonical Documentation Separation: PASS - planning artifacts remain under `specs/246-animate-shimmer-motion/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim is planned; the shared shimmer contract will be updated directly if tests expose gaps.

## Project Structure

### Documentation (this feature)

```text
specs/246-animate-shimmer-motion/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer-motion.md
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
│   ├── task-detail.tsx
│   ├── tasks-list.test.tsx
│   └── tasks-list.tsx
├── styles/
│   └── mission-control.css
└── utils/
    ├── executionStatusPillClasses.test.ts
    └── executionStatusPillClasses.ts
```

**Structure Decision**: Reuse the shared Mission Control status-pill helper, stylesheet, and existing task list/detail surfaces. Do not introduce a separate status-pill component or page-local shimmer implementation for MM-490.

## Complexity Tracking

No constitution violations or added architectural complexity.
