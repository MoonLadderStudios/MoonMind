# Tasks: Skill Zip Import

**Input**: Design documents from `/specs/218-skill-zip-import/`
**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py -k 'skill_import_api or upload_dashboard_skill_zip'`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/skills.test.tsx`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Preserve canonical Jira brief for MM-397 in `docs/tmp/jira-orchestration-inputs/MM-397-moonspec-orchestration-input.md`.
- [X] T002 Create Moon Spec artifacts for MM-397 in `specs/218-skill-zip-import/`.

## Phase 2: Foundational

- [X] T003 Confirm existing Skills Page upload and backend route are partial in `frontend/src/entrypoints/skills.tsx` and `api_service/api/routers/task_dashboard.py`.
- [X] T004 Confirm existing local skill mirror helpers are the runtime storage boundary.

## Phase 3: Story - Import Skill Zip From Skills Page

**Summary**: As a Skills Page user, I want to upload a zip file containing one valid skill bundle so MoonMind validates and saves it as a local skill without requiring manual filesystem setup.

**Independent Test**: Submit valid and invalid zip uploads through the Skills Page and canonical import API, then verify valid bundles are saved under the local skills mirror and invalid archives or manifests are rejected without leaving a partial skill directory.

**Traceability**: FR-001..013, DESIGN-REQ-001..011, SC-001..004

### Unit Tests

- [X] T005 Add failing backend tests for `/api/skills/imports` saved metadata, `skill.md` normalization, manifest frontmatter validation, name mismatch rejection, and default collision rejection in `tests/unit/api/routers/test_task_dashboard.py`.
- [X] T006 Run focused backend tests and confirm failures are due to missing `/api/skills/imports`.

### Integration Tests

- [X] T007 Update Skills Page test expectations so zip upload posts to `/api/skills/imports` in `frontend/src/entrypoints/skills.test.tsx`.
- [X] T008 Attempt focused frontend validation before implementation; direct `npm run ui:test -- frontend/src/entrypoints/skills.test.tsx` was blocked before dependency preparation because `vitest` was not installed.

### Implementation

- [X] T009 Implement canonical skill import response model and route in `api_service/api/routers/task_dashboard.py`.
- [X] T010 Implement archive limits and safety validation alignment in `api_service/api/routers/task_dashboard.py`.
- [X] T011 Implement manifest frontmatter validation, `skill.md` support, parent-directory matching, and canonical `SKILL.md` save normalization in `api_service/api/routers/task_dashboard.py`.
- [X] T012 Update Skills Page upload flow to call `/api/skills/imports` and select `response.name` in `frontend/src/entrypoints/skills.tsx`.

### Story Validation

- [X] T013 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py -k 'skill_import_api or upload_dashboard_skill_zip'` and verify focused backend tests pass.
- [X] T014 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/skills.test.tsx` and verify runner-integrated validation passes after dependency preparation.

## Phase 4: Polish And Verification

- [X] T015 Compile `api_service/api/routers/task_dashboard.py`.
- [X] T016 Run final `./tools/test_unit.sh` for full unit validation or document the exact local blocker.
- [X] T017 Run final `/moonspec-verify` and record the verdict for MM-397.

## Dependencies & Execution Order

- T005-T008 before T009-T012.
- T009-T011 before T012.
- T013-T017 after implementation.

## Implementation Strategy

Harden the existing upload implementation behind a shared import helper, expose the canonical `/api/skills/imports` contract, keep invalid uploads fail-closed, and preserve the existing local skill mirror storage model.
