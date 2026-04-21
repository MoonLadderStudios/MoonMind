# Tasks: Show Recent Manifest Runs

**Input**: Design documents from `/specs/217-show-recent-manifest-runs/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Test Commands**:

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Create Moon Spec artifacts for MM-421 in `specs/217-show-recent-manifest-runs/`.
- [X] T002 Preserve canonical Jira brief for MM-421 in `docs/tmp/jira-orchestration-inputs/MM-421-moonspec-orchestration-input.md`.

## Phase 2: Foundational

- [X] T003 Confirm existing Manifests page requests `/api/executions?entry=manifest&limit=200` and renders Recent Runs below Run Manifest in `frontend/src/entrypoints/manifests.tsx` and `frontend/src/entrypoints/manifests.test.tsx`.
- [X] T004 Confirm existing Recent Runs table lacks MM-421 display columns and filters in `frontend/src/entrypoints/manifests.tsx`.

## Phase 3: Story - Monitor Recent Manifest Runs

**Summary**: As a dashboard user, I want recent manifest runs visible below the Run Manifest card so I can immediately check start state, current stage, result, timing, and details for manifest executions.

**Independent Test**: Open `/tasks/manifests` with manifest execution history returned by `/api/executions?entry=manifest&limit=200`, verify the Run Manifest form appears before Recent Runs, verify the Recent Runs surface shows run identity/detail link, manifest label, action, status with current stage when present, started time, duration, and row actions, then filter by status, manifest name, and search text and verify the visible list updates.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-001..009, SC-001..005

### Unit Tests

- [X] T005 Add failing frontend row-display tests for run details, manifest label, action, status-stage detail, started time, duration, and View details action in `frontend/src/entrypoints/manifests.test.tsx` (FR-003, FR-004, FR-005, FR-009, SC-002, SC-004, DESIGN-REQ-003..005, DESIGN-REQ-009).
- [X] T006 Add failing frontend filter and empty-state tests for status, manifest, and free-text search in `frontend/src/entrypoints/manifests.test.tsx` (FR-006, FR-007, SC-003, DESIGN-REQ-006, DESIGN-REQ-007).
- [X] T007 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx` and confirm T005-T006 fail for the expected Recent Runs gaps.

### Integration Tests

- [X] T008 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx` and confirm the new UI tests fail before implementation.

### Implementation

- [X] T009 Extend manifest execution row schema and display formatters in `frontend/src/entrypoints/manifests.tsx` (FR-003, FR-004, FR-005).
- [X] T010 Add status, manifest, and search filters to Recent Runs in `frontend/src/entrypoints/manifests.tsx` (FR-006, FR-009).
- [X] T011 Update Recent Runs table columns, detail links, status-stage display, fallback values, and manifest-specific empty state in `frontend/src/entrypoints/manifests.tsx` (FR-003, FR-004, FR-005, FR-007, FR-008, FR-009).

### Story Validation

- [X] T012 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx` and verify the story passes.
- [X] T013 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx` and verify runner-integrated UI validation passes.

## Phase 4: Polish And Verification

- [X] T014 Update `docs/UI/ManifestsPage.md` only if implementation behavior diverges from the desired-state document.
- [X] T015 Run `./tools/test_unit.sh` for final unit validation or document the exact local blocker.
- [X] T016 Run final `/moonspec-verify` and record the verdict for MM-421.

## Dependencies & Execution Order

- T005-T008 must run before T009-T011.
- T009-T011 all touch `frontend/src/entrypoints/manifests.tsx` and should be sequenced.
- T012-T016 run after implementation.

## Implementation Strategy

Existing MM-419 work already unified the Manifests page and added the Run Manifest form. MM-421 closes the Recent Runs observability gap with frontend tests first, then UI-only changes that keep the backend contract unchanged and preserve MM-421 traceability through final verification.
