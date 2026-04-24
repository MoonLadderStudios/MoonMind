# Tasks: Preview and Download Task Images by Target

**Input**: Design documents from `/specs/201-preview-download-task-images/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: MM-373, DESIGN-REQ-015, DESIGN-REQ-018, FR-001 through FR-009.

**Test Commands**:

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-373 is a runtime single-story request in.
- [X] T002 Create Moon Spec artifacts in specs/201-preview-download-task-images/.

## Phase 2: Foundational

- [X] T003 Verify existing artifact list response exposes metadata needed for target-aware rendering in moonmind/schemas/temporal_artifact_models.py and api_service/api/routers/temporal_artifacts.py.
- [X] T004 Verify existing edit/rerun draft reconstruction preserves persisted refs in frontend/src/lib/temporalTaskEditing.ts.

## Phase 3: Story - Target-Aware Image Review

**Summary**: As a task reviewer, I want task detail, edit, and rerun surfaces to show task image inputs by persisted target so I can preview, download, and audit attachments.

**Independent Test**: Load artifact metadata with objective and step image attachments, force preview failure, and confirm metadata plus MoonMind-owned download links remain visible.

**Traceability**: MM-373, DESIGN-REQ-015, DESIGN-REQ-018, FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004.

### Unit Tests

- [X] T005 Add failing task-detail UI test for target-grouped objective and step image inputs, MoonMind-owned download links, and preview failure fallback in frontend/src/entrypoints/task-detail.test.tsx.
- [X] T006 Confirm existing edit/rerun tests cover persisted refs, explicit removal, and persisted-vs-local attachment distinctions in frontend/src/entrypoints/task-create.test.tsx.

### Integration Tests

- [X] T007 Confirm the focused UI tests exercise the task-detail artifact list integration boundary and edit/rerun draft reconstruction flow in frontend/src/entrypoints/task-detail.test.tsx and frontend/src/entrypoints/task-create.test.tsx.
- [X] T008 Confirm the broader unit runner covers existing FastAPI artifact route contract tests relevant to MoonMind-owned download endpoints through ./tools/test_unit.sh.

### Red-first Confirmation

- [X] T009 Confirm the new task-detail test asserts behavior absent from the previous implementation: target-grouped Input Images, MoonMind-owned image URLs despite external download_url metadata, and preview failure fallback in frontend/src/entrypoints/task-detail.test.tsx.
- [X] T010 Confirm existing edit/rerun regression tests fail if unchanged persisted refs are omitted or removed without explicit user action in frontend/src/entrypoints/task-create.test.tsx.

### Implementation

- [X] T011 Parse artifact metadata in frontend/src/entrypoints/task-detail.tsx for task image input grouping.
- [X] T012 Render Input Images groups by objective and step targets in frontend/src/entrypoints/task-detail.tsx.
- [X] T013 Use MoonMind-owned `/api/artifacts/{artifactId}/download` URLs for task image preview and download controls in frontend/src/entrypoints/task-detail.tsx.
- [X] T014 Preserve metadata and download actions when task image preview fails in frontend/src/entrypoints/task-detail.tsx.
- [X] T015 Keep generic artifact table behavior for non-input artifacts and explicit non-input download URLs in frontend/src/entrypoints/task-detail.tsx.

### Story Validation

- [X] T016 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`.
- [X] T017 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` if local dependencies and time allow.

## Phase 4: Polish and Final Verification

- [X] T018 Run `/moonspec-verify`-style read-only verification against MM-373, spec.md, tasks.md, and test evidence.

## Dependencies & Execution Order

- T001-T004 before story implementation.
- T005-T008 before T009-T010 red-first confirmation.
- T009-T010 before T011-T015 implementation.
- T016-T017 before T018 final verification.

## Implementation Strategy

Use existing task detail artifact data. Only group images when authoritative dashboard attachment metadata identifies the target. Keep edit/rerun behavior scoped to existing reconstruction and persisted ref tests.
