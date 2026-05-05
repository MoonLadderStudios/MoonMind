# Implementation Plan: Shared Additive Shimmer Masks

**Branch**: `301-shared-additive-shimmer` | **Date**: 2026-05-05 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/spec.md`

## Summary

Active Mission Control status pills need one phase-locked additive shimmer light field exposed through fill, border, and text masks. The current repository already contains the runtime implementation in `frontend/src/styles/mission-control.css`, label support in `frontend/src/components/ExecutionStatusPill.tsx`, and CSS/entrypoint tests in `frontend/src/entrypoints/mission-control.test.tsx`, `tasks-list.test.tsx`, and `task-detail.test.tsx`. Planning therefore records the implemented evidence, keeps final verification explicit, and leaves no planned production code changes unless verification later exposes drift.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/styles/mission-control.css` defines `--mm-executing-moving-light-gradient`, shared `::before` fill mask, `::after` border mask, and `.status-letter-wave::after` text mask; `frontend/src/entrypoints/mission-control.test.tsx` asserts the shared gradient and animation across mask layers. | no new implementation | final unit verification |
| FR-002 | implemented_verified | `.status-letter-wave::after` uses `content: attr(data-label)`, background clipping, transparent text fill, and the shared moving light field; `ExecutionStatusPill.tsx` provides `data-label`. | no new implementation | final unit verification |
| FR-003 | implemented_verified | Selectors remain scoped to `.status-running[data-effect="shimmer-sweep"]`, `.is-executing`, and `.is-planning`; task-list and detail tests assert only active states receive shimmer metadata. | no new implementation | final unit + integration verification |
| FR-004 | implemented_verified | Reduced-motion CSS disables pseudo-element, text-mask, and glyph animation while preserving static active background; CSS contract tests assert reduced-motion animation removal. | no new implementation | final unit verification |
| FR-005 | implemented_verified | No JavaScript timer or render-loop animation is present; CSS keyframes own shimmer motion and React only renders static label/glyph markup. | no new implementation | final unit verification |
| FR-006 | implemented_verified | `@supports not ((background-clip: text) or (-webkit-background-clip: text))` disables the text mask and re-enables `mm-executing-letter-brighten` on glyph spans. | no new implementation | final unit verification |
| FR-007 | implemented_verified | `docs/UI/EffectShimmerSweep.md` describes the shared additive light field and the phase-locked fill, border, and text masks. | no new implementation | final documentation review |
| FR-008 | implemented_verified | `ExecutionStatusPill.tsx` preserves the existing accessible label, visible grapheme spans, and `executionStatusPillProps()` metadata boundary; list/detail tests assert text, aria label, and glyph shape. | no new implementation | final unit + integration verification |
| SCN-001 | implemented_verified | CSS test asserts fill, border, and text use the same moving light field. | no new implementation | final unit verification |
| SCN-002 | implemented_verified | CSS and entrypoint tests assert label data, glyph spans, and text mask contract. | no new implementation | final unit + integration verification |
| SCN-003 | implemented_verified | CSS test asserts `mix-blend-mode: plus-lighter`, isolation, and screen fallback for unsupported additive blend. | no new implementation | final unit verification |
| SCN-004 | implemented_verified | CSS test asserts reduced-motion animation shutdown and static positioning. | no new implementation | final unit verification |
| SCN-005 | implemented_verified | CSS test asserts fallback glyph animation remains defined and active only in the unsupported text-mask branch. | no new implementation | final unit verification |
| SCN-006 | implemented_verified | Existing task-list and detail tests assert active selector attachment; `executionStatusPillProps` tests cover non-active state isolation. | no new implementation | final unit + integration verification |
| SC-001 | implemented_verified | CSS contract tests assert shared fill and border masks. | no new implementation | final unit verification |
| SC-002 | implemented_verified | CSS contract tests assert text-clipped shimmer overlay. | no new implementation | final unit verification |
| SC-003 | implemented_verified | CSS contract tests assert reduced-motion animation removal. | no new implementation | final unit verification |
| SC-004 | implemented_verified | CSS contract tests assert unsupported text-mask fallback. | no new implementation | final unit verification |
| SC-005 | implemented_verified | Documentation now states the one-sweep, multiple-mask desired state. | no new implementation | final documentation review |
| DESIGN-REQ-001 | implemented_verified | Shared light-field token and three mask layers are present in CSS and covered by CSS tests. | no new implementation | final unit verification |
| DESIGN-REQ-002 | implemented_verified | Animation is CSS-only after render; no JavaScript animation loop is introduced. | no new implementation | final unit verification |
| DESIGN-REQ-003 | implemented_verified | `docs/UI/EffectShimmerSweep.md` updated to declarative shared additive light-field model. | no new implementation | final documentation review |
| DESIGN-REQ-004 | implemented_verified | Reduced-motion and unsupported text-mask fallback branches are present and covered by CSS tests. | no new implementation | final unit verification |
| DESIGN-REQ-005 | implemented_verified | Existing selector contract remains centralized through `executionStatusPillProps()` and active-state CSS selectors. | no new implementation | final unit + integration verification |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; CSS for visual effect; Python 3.12 remains present in the repository but is not part of this story.  
**Primary Dependencies**: React, existing Mission Control stylesheet, Vitest, Testing Library, PostCSS test helpers, existing `executionStatusPillProps()` helper.  
**Storage**: N/A; no persistence or data-store changes.  
**Unit Testing**: Vitest CSS contract tests and focused component/entrypoint assertions via `./tools/test_unit.sh --dashboard-only --ui-args ...`.  
**Integration Testing**: Vitest/Testing Library Mission Control entrypoint render tests for task list and task detail surfaces through `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`; no compose-backed integration is required because this is a frontend-only visual contract.  
**Target Platform**: Modern browsers running Mission Control, with reduced-motion and forced-colors fallbacks.  
**Project Type**: Frontend web application within the MoonMind monorepo.  
**Performance Goals**: CSS-only animation after render; no React timer rerenders; no status-pill layout shift.  
**Constraints**: Preserve status text, accessibility labels, layout dimensions, existing selector contract, reduced-motion behavior, and canonical docs as desired-state documentation.  
**Scale/Scope**: One shared status-pill visual treatment across list, card, and detail Mission Control surfaces.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Rationale |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Frontend-only visual treatment; no agent orchestration behavior changes. |
| II. One-Click Agent Deployment | PASS | No deployment prerequisites, services, secrets, or compose changes. |
| III. Avoid Vendor Lock-In | PASS | No vendor-specific integration or proprietary dependency. |
| IV. Own Your Data | PASS | No data ingestion, persistence, or external storage changes. |
| V. Skills Are First-Class and Easy to Add | PASS | No executable or agent skill runtime contract changes. |
| VI. Delete-Friendly Scaffolding | PASS | Reuses existing status-pill boundaries and keeps behavior covered by tests. |
| VII. Runtime Configurability | PASS | No operator runtime setting is needed for this visual contract. |
| VIII. Modular and Extensible Architecture | PASS | Change stays within existing component, stylesheet, doc, and tests. |
| IX. Resilient by Default | PASS | No workflow/activity/signal payload or retry behavior changes. |
| X. Facilitate Continuous Improvement | PASS | Plan records deterministic verification commands and artifact traceability. |
| XI. Spec-Driven Development | PASS | Spec and planning artifacts exist under `specs/301-shared-additive-shimmer/`. |
| XII. Canonical Documentation Desired State | PASS | Canonical doc describes target semantics; implementation detail remains in spec artifacts and tests. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or legacy internal contracts are introduced; fallback is browser capability handling, not an internal legacy path. |

Post-Phase 1 re-check: PASS. `research.md`, `data-model.md`, `contracts/status-pill-shimmer.md`, and `quickstart.md` preserve the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/301-shared-additive-shimmer/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── status-pill-shimmer.md
├── checklists/
│   └── requirements.md
└── tasks.md              # Phase 2 output, not created by /speckit.plan
```

### Source Code (repository root)

```text
docs/UI/
└── EffectShimmerSweep.md

frontend/src/
├── components/
│   └── ExecutionStatusPill.tsx
├── entrypoints/
│   ├── mission-control.test.tsx
│   ├── task-detail.test.tsx
│   └── tasks-list.test.tsx
├── styles/
│   └── mission-control.css
└── utils/
    ├── executionStatusPillClasses.ts
    └── executionStatusPillClasses.test.ts
```

**Structure Decision**: Use the existing Mission Control frontend structure. The visual contract belongs in the shared stylesheet, the label hook belongs in the existing status-pill component, and verification belongs in CSS contract plus list/detail render tests.

## Complexity Tracking

No constitution violations or structural complexity exceptions are required.
