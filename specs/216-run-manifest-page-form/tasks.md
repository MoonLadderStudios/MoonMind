# Tasks: Run Manifest Page Form

**Input**: Design documents from `/specs/216-run-manifest-page-form/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Test Commands**:

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Create Moon Spec artifacts for MM-419 in `specs/216-run-manifest-page-form/`.
- [X] T002 Preserve canonical Jira brief for MM-419 in `spec.md` (Input).

## Phase 2: Foundational

- [X] T003 Confirm existing manifest run backend options support dry run, force full, and max docs but no priority field in `api_service/api/schemas.py`.
- [X] T004 Confirm existing Manifests page and tests cover unified layout, source modes, mode value preservation, and in-place refresh in `frontend/src/entrypoints/manifests.tsx` and `frontend/src/entrypoints/manifests.test.tsx`.

## Phase 3: Story - Run Manifest From Manifests Page

**Summary**: As a dashboard user, I want a compact Run Manifest form on the Manifests page that supports registry names and inline YAML so I can start either kind of manifest run from the same context.

**Independent Test**: Open `/tasks/manifests`, exercise registry and inline source modes, submit valid runs through the existing manifest run flow, and verify invalid names, empty YAML, invalid max docs, and raw secret-shaped values are rejected before submission.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-001..007, SC-001..004

### Unit Tests

- [X] T005 Add failing frontend validation tests for invalid max docs and raw secret-shaped input in `frontend/src/entrypoints/manifests.test.tsx` (FR-005, FR-006, SC-001, DESIGN-REQ-004).
- [X] T006 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx` and confirm T005 fails for the expected validation gaps.

### Integration Tests

- [X] T007 Add runner-integrated UI validation coverage for valid env/Vault-style references and existing valid submissions in `frontend/src/entrypoints/manifests.test.tsx` (FR-006, FR-007, SC-002).
- [X] T008 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx` and confirm new validation tests fail before implementation.

### Implementation

- [X] T009 Implement client-side max docs validation before manifest API calls in `frontend/src/entrypoints/manifests.tsx` (FR-005).
- [X] T010 Implement client-side raw secret-shaped value rejection before manifest API calls in `frontend/src/entrypoints/manifests.tsx` (FR-006).

### Story Validation

- [X] T011 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx` and verify the story passes.
- [X] T012 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx` and verify runner-integrated UI validation passes.

## Phase 4: Polish And Verification

- [X] T013 Update `docs/UI/ManifestsPage.md` only if implementation behavior diverges from the desired-state document.
- [X] T014 Run `./tools/test_unit.sh` for final unit validation or document the exact local blocker.
- [X] T015 Run final `/moonspec-verify` and record the verdict for MM-419.

## Dependencies & Execution Order

- T005-T008 must run before T009-T010.
- T009 and T010 both touch `frontend/src/entrypoints/manifests.tsx` and should be sequenced.
- T011-T015 run after implementation.

## Implementation Strategy

Existing MM-418 work already implemented most MM-419 behavior. This story closes the remaining validation gaps with tests first, keeps backend contracts unchanged, and preserves MM-419 traceability through final verification.
