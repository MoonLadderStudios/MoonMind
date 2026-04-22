# Implementation Plan: Reduced Motion, Accessibility, and Performance Guardrails

**Branch**: `237-reduced-motion-accessibility-performance` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/237-reduced-motion-accessibility-performance/spec.md`

## Summary

Implement MM-468 by tightening reduced-motion, accessibility, and performance guardrails on the existing `MaskedConicBorderBeam` runtime surface. The component already exposes reduced-motion modes and transform-based orbit CSS from MM-465 through MM-467, but MM-468 still needs focused evidence and small runtime refinements: default/custom accessible status labels, static border-only minimal mode, auto reduced-motion glow degradation, MM-468 traceability, and CSS contract tests for performance and non-goals.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | CSS tests verify auto reduced-motion stops animation, keeps a static primary layer, and hides optional glow/companion layers first | complete | focused unit passed |
| FR-002 | implemented_verified | CSS tests verify minimal mode hides beam/glow/companion layers and brightens the static border ring | complete | focused unit passed |
| FR-003 | implemented_verified | CSS non-goal tests verify rapid-pulse replacement is absent | complete | focused unit passed |
| FR-004 | implemented_verified | component tests verify default `Executing` label, custom label, label suppression, and inactive omission | complete | focused unit passed |
| FR-005 | implemented_verified | existing and MM-468 tests verify decorative beam, glow, and companion layers are `aria-hidden` | complete | focused unit passed |
| FR-006 | implemented_verified | CSS tests verify modest opacity/glow tokens and absence of red/orange warning pulse terms | complete | focused unit passed |
| FR-007 | implemented_verified | CSS tests verify linear infinite orbit uses transform keyframes and animated declarations avoid layout-triggering properties | complete | focused unit passed |
| FR-008 | implemented_verified | CSS tests verify auto reduced-motion hides glow/companion before removing the primary static cue | complete | focused unit passed |
| FR-009 | implemented_verified | existing border-ring mask/content tests and MM-468 reduced-motion tests verify content separation | complete | focused unit passed |
| FR-010 | implemented_verified | CSS non-goal tests reject shimmer, spinner, completion pulse, success burst, rapid pulse, warning pulse, and content-area mask terms | complete | focused unit passed |
| FR-011 | implemented_verified | traceability test verifies MM-468 and DESIGN-REQ-013/014/015 are exported | complete | focused unit passed |
| SC-001 | implemented_verified | status-label component test covers default, custom, suppressed, inactive, and decorative `aria-hidden` behavior | complete | focused unit passed |
| SC-002 | implemented_verified | auto reduced-motion CSS test covers static primary cue and glow/companion degradation | complete | focused unit passed |
| SC-003 | implemented_verified | minimal CSS test covers hidden visual layers and brighter static border ring | complete | focused unit passed |
| SC-004 | implemented_verified | performance CSS test covers transform keyframes and no layout-triggering animated declarations | complete | focused unit passed |
| SC-005 | implemented_verified | non-goal CSS test covers warning/pulse/shimmer/spinner/completion exclusions | complete | focused unit passed |
| SC-006 | implemented_verified | existing component integration tests and MM-468 focused validation verify content separation | complete | focused unit passed |
| SC-007 | implemented_verified | spec, plan, tasks, traceability export, and verification artifact preserve MM-468 | complete | focused unit passed |
| DESIGN-REQ-013 | implemented_verified | auto/minimal reduced-motion CSS tests verify static cue and border-ring-only minimal mode | complete | focused unit passed |
| DESIGN-REQ-014 | implemented_verified | component and CSS tests verify non-visual status label, decorative layers, calm treatment, and warning-pulse exclusions | complete | focused unit passed |
| DESIGN-REQ-015 | implemented_verified | CSS tests verify transform animation, no layout-triggering animated declarations, modest glow, and glow-first degradation | complete | focused unit passed |
| DESIGN-REQ-016 | implemented_verified | non-goal and border-only/content separation tests remain passing | complete | focused unit passed |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, Vitest, Testing Library, PostCSS test parsing, existing Mission Control stylesheet  
**Storage**: No new persistent storage  
**Unit Testing**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/components/MaskedConicBorderBeam.test.tsx` and final `./tools/test_unit.sh`  
**Integration Testing**: Component-level integration in `frontend/src/components/MaskedConicBorderBeam.test.tsx`; required hermetic integration suite is not expected because this is isolated frontend visual behavior  
**Target Platform**: Mission Control web UI in modern browsers  
**Project Type**: Frontend component and stylesheet refinement  
**Performance Goals**: Preserve transform-based linear orbit animation, avoid layout-triggering animation, and disable optional glow first in reduced/degraded modes  
**Constraints**: Preserve border-only mask and content readability; no new package or service dependency; runtime mode only; no docs-only substitution  
**Scale/Scope**: One reusable component, one focused test file, and existing Mission Control CSS

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - no agent orchestration logic changes.
- II. One-Click Agent Deployment: PASS - no deployment dependency changes.
- III. Avoid Vendor Lock-In: PASS - frontend component remains provider-neutral.
- IV. Own Your Data: PASS - no external data flow changes.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - tests define the visual and accessibility contract.
- VII. Runtime Configurability: PASS - behavior is controlled through component props and CSS custom properties.
- VIII. Modular and Extensible Architecture: PASS - changes stay inside the existing component boundary.
- IX. Resilient by Default: PASS - no workflow/runtime contract changes.
- X. Facilitate Continuous Improvement: PASS - final verification artifact required.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, and verification artifacts are created.
- XII. Canonical Documentation Separation: PASS - implementation notes stay under `specs/` and `docs/tmp`.
- XIII. Pre-Release Compatibility: PASS - no compatibility shims; the component contract is updated directly.

## Project Structure

```text
specs/237-reduced-motion-accessibility-performance/
├── checklists/requirements.md
├── contracts/masked-conic-border-beam-guardrails.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
└── tasks.md

frontend/src/components/
├── MaskedConicBorderBeam.tsx
└── MaskedConicBorderBeam.test.tsx

frontend/src/styles/
└── mission-control.css
```

**Structure Decision**: Extend the existing `MaskedConicBorderBeam` component, focused test file, and Mission Control stylesheet created by MM-465 through MM-467. Do not create a second component or adopt the effect on unrelated Mission Control surfaces in this story.

## Complexity Tracking

No constitution violations or added architectural complexity.
