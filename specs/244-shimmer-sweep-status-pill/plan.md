# Implementation Plan: Shared Executing Shimmer for Status Pills

**Branch**: `244-shimmer-sweep-status-pill` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/244-shimmer-sweep-status-pill/spec.md`

## Summary

Implement MM-488 by extending the existing shared `status-running` pill presentation into a reusable executing shimmer modifier that applies consistently across task list, card, and detail surfaces. The repo already has shared executing-status class mapping and shared status-pill styling, but it does not yet expose the shimmer selector contract, reduced-motion fallback, or verification coverage required by `docs/UI/EffectShimmerSweep.md`. Planned work is focused frontend UI code plus Vitest coverage: add the shared modifier contract, adopt it on executing pills without changing text or layout, add a reduced-motion fallback, and preserve MM-488 traceability.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/utils/executionStatusPillClasses.ts`, `frontend/src/styles/mission-control.css` expose a shared `status-running` pill but no shimmer treatment | add shared executing shimmer modifier to shared status-pill styling and adopt it on executing pills | unit + integration |
| FR-002 | missing | no `data-state="executing"`, `data-effect="shimmer-sweep"`, or `.is-executing` contract found in frontend status-pill markup or CSS | add preferred/fallback selector contract and apply it to supported executing pills | unit + integration |
| FR-003 | partial | non-executing pills use distinct semantic classes today, but no shimmer isolation contract exists yet | scope shimmer selectors strictly to executing state and add regression tests for non-executing states | unit + integration |
| FR-004 | implemented_unverified | list/detail status pills currently render raw status text inside shared spans without mutating text content | verify text and icon content remain unchanged after shimmer adoption; adjust markup only if needed to preserve semantics | integration |
| FR-005 | implemented_unverified | current pills are lightweight inline spans with shared styling and no extra wrappers | verify shimmer modifier does not add layout shift or alter polling/live-update behavior; implement conservative pseudo-element approach if needed | unit + integration |
| FR-006 | missing | no status-pill reduced-motion shimmer replacement exists in `mission-control.css` | add non-animated reduced-motion active treatment and verify it remains executing-only | unit + integration |
| FR-007 | partial | current executing pill color is cyan via `.status-running`, but no shimmer-specific guardrail or tests exist | tune shimmer visuals to read as active progress and add CSS contract assertions for calm active treatment | unit |
| FR-008 | partial | task list, card, and task detail already reuse shared `executionStatusPillClasses`, but not a shared shimmer modifier contract | wire the shared modifier into all supported surfaces through common helper/class usage and page render tests | integration |
| FR-009 | missing | MM-488 traceability exists in spec only; no plan/test/export evidence exists yet | add MM-488 traceability expectations to tests and downstream artifacts | unit |
| SCN-001 | partial | executing pills already share `status-running`, but no shimmer effect exists | verify list/card/detail executing pills render shared shimmer modifier | integration |
| SCN-002 | missing | no preferred selector or fallback marker coverage exists | verify both selector paths activate the same modifier | unit + integration |
| SCN-003 | partial | non-executing pills already render with other semantic classes | verify shimmer stays off for non-executing states | unit + integration |
| SCN-004 | implemented_unverified | current status pills keep text content and layout stable | add render and CSS regression checks for text, icon, and layout stability | integration |
| SCN-005 | missing | no reduced-motion executing-pill treatment exists | add and verify non-animated reduced-motion active treatment | unit + integration |
| SC-001 | partial | shared executing-status classes exist across surfaces | add shimmer-specific render and CSS assertions across supported surfaces | unit + integration |
| SC-002 | missing | no selector-path verification exists | add tests for preferred and fallback hooks | unit + integration |
| SC-003 | partial | non-executing pills already map to other semantic classes | add executing-only shimmer regression tests | unit + integration |
| SC-004 | implemented_unverified | existing pills are simple spans and live updates already work | verify no text/layout/live-update regressions while adopting shimmer | integration |
| SC-005 | missing | no reduced-motion pill fallback exists | add reduced-motion CSS contract and render assertions | unit + integration |
| SC-006 | missing | MM-488 is not yet represented in plan/test evidence beyond spec | add traceability assertions and preserve MM-488 through downstream artifacts | unit |
| DESIGN-REQ-001 | partial | shared running pill color exists but not the shimmer semantics | add calm active shimmer treatment and guardrail tests against warning/error reads | unit |
| DESIGN-REQ-002 | partial | current status pill is isolated from task-row layout and polling behavior | keep work limited to status-pill modifier activation and verify no unrelated UI behavior changes | integration |
| DESIGN-REQ-003 | missing | host contract selectors are absent from pill markup and CSS | add preferred and fallback hook support | unit + integration |
| DESIGN-REQ-004 | implemented_unverified | current pill markup preserves text and pill footprint | verify shimmer remains additive and layout-neutral | unit + integration |
| DESIGN-REQ-011 | missing | no shared shimmer modifier contract exists yet | add shared status-pill modifier CSS rather than page-local animation | unit + integration |
| DESIGN-REQ-013 | missing | no reduced-motion shimmer replacement exists | add static reduced-motion highlight treatment | unit + integration |
| DESIGN-REQ-016 | partial | executing maps to shared running class today; finalizing also maps there in helper | narrow shimmer activation to explicit executing contract without changing broader status taxonomy unless required by implementation evidence | unit + integration |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, existing Mission Control stylesheet, existing entrypoint render tests  
**Storage**: No new persistent storage  
**Unit Testing**: Focused Vitest CSS/helper coverage via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` plus final `./tools/test_unit.sh`  
**Integration Testing**: Frontend entrypoint render tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed `integration_ci` suite is expected for this isolated UI behavior  
**Target Platform**: Mission Control web UI in modern desktop and mobile browsers  
**Project Type**: Frontend shared stylesheet and status-pill rendering refinement  
**Performance Goals**: Preserve status-pill readability, avoid measurable layout shift, keep the effect bounded to existing pill surfaces, and use lightweight pseudo-element animation with reduced-motion fallback  
**Constraints**: Runtime mode only; no page-local animation forks; no wrapper that changes pill dimensions; preserve text/icon/live-update behavior; preserve MM-488 traceability; keep unit and integration strategies explicit  
**Scale/Scope**: Shared status-pill helper/CSS plus task list and task detail surfaces, with focused frontend tests only

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent/runtime orchestration changes.
- II. One-Click Agent Deployment: PASS - no deployment or startup dependency changes.
- III. Avoid Vendor Lock-In: PASS - UI behavior remains provider-neutral.
- IV. Own Your Data: PASS - no data ingestion or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - the visual behavior is defined through shared UI contract and tests, not bespoke page logic.
- VII. Runtime Configurability: PASS - behavior remains driven by runtime state and CSS/media-query conditions rather than hardcoded per-page branching.
- VIII. Modular and Extensible Architecture: PASS - work stays inside existing shared status-pill helper, entrypoints, and stylesheet.
- IX. Resilient by Default: PASS - no workflow or persisted payload contract changes.
- X. Facilitate Continuous Improvement: PASS - traceability and verification artifacts remain explicit.
- XI. Spec-Driven Development: PASS - spec, plan, and design artifacts are present before task generation.
- XII. Canonical Documentation Separation: PASS - implementation planning stays under `specs/`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shim is planned; the shared status-pill contract will be updated directly.

## Project Structure

### Documentation (this feature)

```text
specs/244-shimmer-sweep-status-pill/
├── checklists/requirements.md
├── contracts/
│   └── status-pill-shimmer.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
└── tasks.md
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
    └── executionStatusPillClasses.ts
```

**Structure Decision**: Extend the existing shared status-pill helper, Mission Control stylesheet, and entrypoint render tests used by task list, card, and detail surfaces. Do not introduce a second status-pill component or a page-local shimmer implementation.

## Complexity Tracking

No constitution violations or added architectural complexity.
