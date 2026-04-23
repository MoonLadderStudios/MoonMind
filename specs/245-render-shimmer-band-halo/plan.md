# Implementation Plan: Themed Shimmer Band and Halo Layers

**Branch**: `245-render-shimmer-band-halo` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/245-render-shimmer-band-halo/spec.md`

## Summary

Plan MM-489 as a focused refinement of the shared executing shimmer already introduced for MM-488. The repo already contains a shared executing shimmer selector contract, shared Mission Control CSS, and list/detail render coverage, but the existing implementation only indirectly proves the layered band-and-halo visual model and still carries MM-488 traceability. Planned work is frontend-only: verify the current shared shimmer against the MM-489 source requirements, add any missing tokenization or layer semantics needed for the band/halo treatment, preserve explicit MM-489 traceability, and extend focused Vitest coverage so unit and integration evidence matches the story’s acceptance criteria.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/styles/mission-control.css` keeps `.status-running` base styling and layers shimmer styling on executing pills, but no MM-489-focused test proves the base appearance remains visibly preserved. | add verification tests for preserved base appearance and adjust CSS only if proof fails | unit + integration |
| FR-002 | implemented_unverified | Executing shimmer CSS already uses two gradient layers with distinct opacity/size values in `frontend/src/styles/mission-control.css`, but current tests assert only that a background image exists. | add tests that prove the bright band and wider dimmer halo are both present; refine layer definitions if assertions expose a gap | unit |
| FR-003 | implemented_unverified | Current shimmer CSS derives colors from `--mm-accent` and `--mm-accent-2`, but no test explicitly proves theme-token binding or light/dark coherence for MM-489. | add CSS contract assertions for token-derived shimmer roles and update styling only if needed | unit |
| FR-004 | implemented_unverified | Task-list and task-detail pills render shimmer additively through existing span content, but no test proves text readability remains primary during the layered treatment. | add render/CSS verification for text readability and stacking assumptions; implement contingency only if proof fails | unit + integration |
| FR-005 | implemented_unverified | Current CSS uses `overflow: hidden` and `isolation: isolate`, and the shimmer is a background treatment rather than a pointer-intercepting overlay, but there is no MM-489-focused verification for bounds, hit-testing, or layout preservation. | add CSS and render assertions for bounded treatment, unchanged layout contract, and non-interference; adjust CSS only if needed | unit + integration |
| FR-006 | partial | Token variables exist for duration, delay, angle, and opacities, but suggested tunable values for width/bounds and equivalent reusable variables are incomplete or hard-coded in the shimmer block. | add missing reusable effect tokens or equivalent variables for the layered treatment and verify their presence | unit |
| FR-007 | missing | Implementation traceability currently preserves `MM-488` in `frontend/src/utils/executionStatusPillClasses.ts` and its tests, not MM-489. | add MM-489 traceability to implementation/test artifacts and preserve it through verification outputs | unit |
| SCN-001 | implemented_unverified | Executing pills render shimmer styling today, but preserved-base behavior is not explicitly proven. | add focused verification and implementation contingency if it fails | integration |
| SCN-002 | implemented_unverified | Dual-layer gradients exist in CSS, but the distinct bright-band and wider-halo semantics are not directly asserted. | add CSS contract tests and refine implementation if needed | unit |
| SCN-003 | implemented_unverified | Existing CSS uses MoonMind accent tokens, but theme-role evidence is indirect. | add token-binding assertions and theme-focused validation | unit |
| SCN-004 | implemented_unverified | The shimmer is background-based and should not intercept input, but this remains unproven at the story level. | add render/CSS checks for interaction and bounds | integration |
| SCN-005 | implemented_unverified | Executing pills are styled inside the existing span contract, but there is no explicit MM-489 proof for bounded fill/border placement with no layout shift. | add layout/bounds assertions and implement only if needed | unit + integration |
| SC-001 | implemented_unverified | Current CSS suggests preserved base behavior, but explicit evidence is missing. | add proof-first tests | unit + integration |
| SC-002 | implemented_unverified | Current CSS suggests two distinct layers, but explicit evidence is missing. | add proof-first tests | unit |
| SC-003 | implemented_unverified | Theme-token usage is present, but explicit evidence is missing. | add proof-first tests | unit |
| SC-004 | implemented_unverified | Readability assumptions are present, but explicit evidence is missing. | add proof-first tests | unit + integration |
| SC-005 | implemented_unverified | Bounds and non-interference assumptions are present, but explicit evidence is missing. | add proof-first tests | unit + integration |
| SC-006 | partial | Some shimmer tokens exist, but the full tunable token surface is incomplete. | add missing tokens/variables and verify them | unit |
| SC-007 | missing | MM-489 traceability does not yet appear in implementation/test artifacts. | add traceability evidence in code/tests and final verification artifacts | unit |
| DESIGN-REQ-005 | implemented_unverified | Theme tokens and calm active styling are present in CSS, but legibility-first behavior is not explicitly proven. | add CSS contract tests and implementation contingency | unit |
| DESIGN-REQ-006 | implemented_unverified | Base + dual-layer shimmer behavior appears implemented in CSS, but the precise visual model is not directly verified. | add dual-layer verification tests and refine only if needed | unit |
| DESIGN-REQ-008 | implemented_unverified | Existing shimmer colors derive from shared tokens, but the role mapping is not directly asserted. | add token-binding verification | unit |
| DESIGN-REQ-009 | implemented_unverified | Isolation behavior is suggested by `overflow: hidden` and `isolation: isolate`, but there is no explicit MM-489 proof for non-interference and layout stability. | add CSS/render verification and contingency fixes | unit + integration |
| DESIGN-REQ-012 | implemented_unverified | The implementation remains an attachable background treatment on existing status pills, but explicit MM-489 verification is missing. | add verification for additive host treatment and bounded placement | unit + integration |
| DESIGN-REQ-015 | partial | Existing variables cover only part of the suggested token block. | add missing reusable variables and tests for their presence | unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, shared status-pill helper, existing entrypoint render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Vitest CSS/helper tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts`  
**Integration Testing**: Vitest entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet and status-pill rendering refinement  
**Performance Goals**: Preserve pill readability, avoid measurable layout shift, keep the effect bounded to the existing pill footprint, and reuse lightweight CSS-only shimmer behavior  
**Constraints**: Runtime mode only; keep the story scoped to the layered shimmer treatment; preserve shared status-pill reuse; preserve MM-489 traceability; keep unit and integration strategies explicit; no page-local forks or layout-changing wrappers  
**Scale/Scope**: Shared status-pill CSS/helper plus task list and task detail surfaces, with focused frontend test coverage only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup changes.
- III. Avoid Vendor Lock-In: PASS - UI behavior remains provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the story is governed by shared UI contracts and focused tests instead of page-local logic.
- VII. Runtime Configurability: PASS - behavior remains driven by state selectors, theme tokens, and media queries.
- VIII. Modular and Extensible Architecture: PASS - work stays within existing shared frontend seams.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - traceability and verification evidence remain explicit.
- XI. Spec-Driven Development: PASS - spec and planning artifacts exist before task generation.
- XII. Canonical Documentation Separation: PASS - planning artifacts remain under `specs/245-render-shimmer-band-halo/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim is planned; shared UI contracts will be updated directly if needed.

## Project Structure

### Documentation (this feature)

```text
specs/245-render-shimmer-band-halo/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer-layers.md
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

**Structure Decision**: Reuse the shared Mission Control status-pill helper, stylesheet, and existing list/detail render surfaces. Do not introduce a separate status-pill component or page-specific shimmer implementation.

## Complexity Tracking

No constitution violations or added architectural complexity.
