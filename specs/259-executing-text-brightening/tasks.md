# Tasks: Executing Text Brightening Sweep

**Input**: Design documents from `specs/259-executing-text-brightening/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/execution-status-pill.md`, `quickstart.md`

## Validation Commands

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`
- Typecheck: `npm run ui:typecheck`
- Lint: `npm run ui:lint`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Source Traceability Summary

- DESIGN-REQ-001 through DESIGN-REQ-008 map to FR-001 through FR-011.
- SCN-001 through SCN-005 are covered by task-list render tests and Mission Control CSS contract tests.
- SC-001 through SC-005 are covered by focused UI tests, typecheck/lint, and final verification.

## Phase 1: Setup

- [X] T001 Verify the active one-story spec, implementation plan, research, data model, contract, and quickstart exist under `specs/259-executing-text-brightening/`.
- [X] T002 Identify the existing status-pill helper, task-list render call sites, Mission Control shimmer CSS, and focused Vitest coverage files.

## Phase 2: Story - Executing Letter Brightening

**Summary**: As a Mission Control user, I want executing task-list status pills to brighten letters in sync with the existing shimmer sweep so active work feels visibly alive without extra polling or layout changes.

**Independent Test**: Render task-list rows in executing and non-executing states, then verify only executing pills use per-glyph visual spans with staggered CSS delays, preserve the shared status-pill metadata and accessible label, keep non-executing pills as plain text, and rely on the shared shimmer duration with reduced-motion suppression.

**Traceability IDs**: FR-001 through FR-011; DESIGN-REQ-001 through DESIGN-REQ-008; SC-001 through SC-005.

### Tests First

- [X] T003 [P] Add task-list render assertions in `frontend/src/entrypoints/tasks-list.test.tsx` for executing glyph markup, parent accessible label, hidden visual glyph wrapper, per-glyph delay properties, full text preservation, and non-executing plain rendering.
- [X] T004 [P] Add Mission Control CSS contract assertions in `frontend/src/entrypoints/mission-control.test.tsx` for physical sweep preservation, glyph-wave CSS, shared duration, brightening keyframes, and reduced-motion glyph suppression.
- [X] T005 Run focused UI tests before implementation to confirm the current workspace cannot execute because `vitest` is not installed directly; defer dependency preparation to `./tools/test_unit.sh`.

### Implementation

- [X] T006 Add `frontend/src/components/ExecutionStatusPill.tsx` with centralized status metadata, visible-label normalization, `Intl.Segmenter` grapheme splitting, right-to-left phase delays, accessible executing parent label, and hidden visual glyph wrapper.
- [X] T007 Replace the table and card task-list status spans in `frontend/src/entrypoints/tasks-list.tsx` with `ExecutionStatusPill` while preserving `row.rawState || row.state || row.status` precedence.
- [X] T008 Update `frontend/src/styles/mission-control.css` to keep the physical host shimmer, remove the old pseudo-element text shimmer from this path, add glyph-wave styles and `mm-executing-letter-brighten`, and suppress glyph animation in reduced motion.

### Story Validation

- [X] T009 Run `npm run ui:typecheck`, `npm run ui:lint`, and the focused UI tests after dependencies are prepared.
- [X] T010 Run `./tools/test_unit.sh` for final unit verification or record the exact local blocker.
- [X] T011 Create `verification.md` with final requirement coverage and test evidence.

## Dependencies and Execution Order

1. T001-T002 establish the existing UI seams.
2. T003-T005 define verification expectations first.
3. T006-T008 implement the component, call sites, and CSS.
4. T009-T011 complete validation and final `/moonspec-verify` evidence.
