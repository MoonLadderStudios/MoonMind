# Implementation Plan: MaskedConicBorderBeam Border-Only Contract

**Branch**: `234-masked-conic-border-beam-contract` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/234-masked-conic-border-beam-contract/spec.md`

## Summary

Implement MM-465 by adding a standalone Mission Control React surface that wraps arbitrary rectangular content with a decorative masked conic border beam. The technical approach is to add a small typed frontend component, global Mission Control CSS for the border-only geometry, and Vitest coverage that verifies the public prop contract, inactive state, reduced-motion behavior, and CSS non-goals before adoption by any specific card.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `MaskedConicBorderBeam` component and render tests | complete | unit passed |
| FR-002 | implemented_verified | typed props, deterministic defaults, data/style assertions | complete | unit passed |
| FR-003 | implemented_verified | active render tests and `masked-conic-border-beam` CSS layers | complete | unit passed |
| FR-004 | implemented_verified | CSS contract tests for conic gradient and ring mask | complete | unit passed |
| FR-005 | implemented_verified | inactive test verifies no beam/glow layers | complete | unit passed |
| FR-006 | implemented_verified | custom input tests assert data attributes and CSS variables | complete | unit passed |
| FR-007 | implemented_verified | minimal mode and `prefers-reduced-motion` CSS tests | complete | unit passed |
| FR-008 | implemented_verified | non-goal CSS tests reject shimmer/spinner/completion/success effects | complete | unit passed |
| FR-009 | implemented_verified | decorative layers use `aria-hidden`; status text remains caller-owned | complete | unit passed |
| FR-010 | implemented_verified | MM-465 preserved in spec, plan, tasks, traceability constant, and verification | complete | traceability passed |
| SC-001 | implemented_verified | component tests verify all declared inputs and defaults | complete | unit passed |
| SC-002 | implemented_verified | inactive-state test verifies no moving layers | complete | unit passed |
| SC-003 | implemented_verified | CSS contract tests verify conic-gradient and border-ring masking | complete | unit passed |
| SC-004 | implemented_verified | reduced-motion tests verify stopped animation paths | complete | unit passed |
| SC-005 | implemented_verified | MM-465 preserved in artifacts and implementation traceability | complete | traceability passed |
| DESIGN-REQ-001 | implemented_verified | active perimeter treatment and non-spinner tests | complete | unit passed |
| DESIGN-REQ-002 | implemented_verified | declared prop contract and defaults tests | complete | unit passed |
| DESIGN-REQ-003 | implemented_verified | masked border-ring CSS contract test | complete | unit passed |
| DESIGN-REQ-010 | implemented_verified | reduced-motion CSS and prop tests | complete | unit passed |
| DESIGN-REQ-016 | implemented_verified | non-goal tests for shimmer, spinner, completion pulse, success burst, and content masking | complete | unit passed |

## Technical Context

**Language/Version**: TypeScript with React 19.2  
**Primary Dependencies**: React, existing Mission Control CSS, Vitest, Testing Library, PostCSS for CSS inspection  
**Storage**: N/A  
**Unit Testing**: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and final `./tools/test_unit.sh`  
**Integration Testing**: Existing UI unit tests exercise component-level integration; no service or database integration is needed for this presentation-only surface  
**Target Platform**: Mission Control web UI  
**Project Type**: Frontend component/runtime UI  
**Performance Goals**: Animation uses transform/rotation-oriented CSS and a bounded number of layers  
**Constraints**: Decorative only, no content-area animation, no full-card shimmer, no spinner replacement, no new package dependency  
**Scale/Scope**: One reusable component, one CSS contract, one focused test file

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - adds UI decoration only, no agent behavior replacement.
- II. One-Click Agent Deployment: PASS - no new services, secrets, or runtime prerequisites.
- III. Avoid Vendor Lock-In: PASS - provider-neutral UI component.
- IV. Own Your Data: PASS - no data movement or storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill contract changes.
- VI. Design for Deletion / Thick Contracts: PASS - exposes a compact component contract that can be replaced without workflow impact.
- VII. Powerful Runtime Configurability: PASS - behavior is controlled by props and CSS variables.
- VIII. Modular and Extensible Architecture: PASS - component is isolated from specific cards.
- IX. Resilient by Default: PASS - no workflow or persisted payload changes.
- X. Facilitate Continuous Improvement: PASS - tests and verification preserve evidence.
- XI. Spec-Driven Development: PASS - this plan follows the MM-465 spec.
- XII. Canonical Documentation Separation: PASS - temporary orchestration artifacts remain under `specs/` and `docs/tmp/`.
- XIII. Pre-release Compatibility Policy: PASS - no internal compatibility aliases are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/234-masked-conic-border-beam-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── masked-conic-border-beam.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/components/
├── MaskedConicBorderBeam.tsx
└── MaskedConicBorderBeam.test.tsx

frontend/src/styles/
└── mission-control.css
```

**Structure Decision**: Keep the reusable component under `frontend/src/components/` and its visual contract in the existing shared Mission Control stylesheet so any entrypoint can adopt the surface later.

## Complexity Tracking

No constitution violations.
