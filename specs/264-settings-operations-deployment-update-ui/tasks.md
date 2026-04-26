# Tasks: Settings Operations Deployment Update UI

**Input**: [spec.md](./spec.md), [plan.md](./plan.md)
**Prerequisites**: Existing deployment operations endpoints and Settings Operations component
**Unit Test Command**: `npm run ui:test -- frontend/src/components/settings/OperationsSettingsSection.test.tsx`
**Integration Test Command**: `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx`

**Source Traceability**: `MM-522`; FR-001 through FR-012; SC-001 through SC-005; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-016, DESIGN-REQ-017.

## Phase 1: Setup

- [X] T001 Confirm existing Settings Operations and deployment endpoint files in `frontend/src/components/settings/OperationsSettingsSection.tsx`, `frontend/src/entrypoints/settings.tsx`, and `api_service/api/routers/deployment_operations.py`. (FR-001, FR-009)
- [X] T002 Create MoonSpec artifacts under `specs/264-settings-operations-deployment-update-ui`. (FR-012, SC-005)

## Phase 2: Foundational

- [X] T003 Confirm backend deployment endpoints already expose state, image target, and submit contracts in `tests/unit/api/routers/test_deployment_operations.py`. (FR-002, FR-003, FR-009)

## Phase 3: Story - Deployment Update Operations Card

**Independent Test**: Render Operations settings with mocked deployment endpoints, submit a mutable-tag update after confirmation, and verify the UI displays state/target/recent context while omitting updater runner controls and raw command-log links.

**Traceability IDs**: FR-001 through FR-012; SC-001 through SC-005; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-016, DESIGN-REQ-017.

- [X] T004 [P] Add failing UI test for rendering the Deployment Update card with current deployment state and no top-level deployment navigation in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-001, FR-002, SC-001, DESIGN-REQ-001)
- [X] T005 [P] Add failing UI test for target defaulting to a recent release tag, mutable-tag warning, allowed modes, and absence of updater runner image controls in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-003, FR-004, FR-005, FR-006, SC-002, SC-004, DESIGN-REQ-002, DESIGN-REQ-016)
- [X] T006 [P] Add failing UI test for reason validation, confirmation content, and typed POST payload in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-007, FR-008, FR-009, SC-003)
- [X] T007 [P] Add failing UI test for recent deployment actions and hidden raw command-log links in `frontend/src/components/settings/OperationsSettingsSection.test.tsx`. (FR-010, FR-011, SC-004, DESIGN-REQ-017)
- [X] T008 Implement deployment state/target queries, card sections, target/mode/options/reason controls, confirmation, submit mutation, recent actions rendering, and gated log links in `frontend/src/components/settings/OperationsSettingsSection.tsx`. (FR-001 through FR-011)
- [X] T009 Run focused UI test command and mark T004-T008 complete when passing. (SC-001 through SC-004)
- [X] T010 Run traceability check for `MM-522` and source design IDs in `specs/264-settings-operations-deployment-update-ui`. (FR-012, SC-005)
- [X] T011 Run `/speckit.verify` equivalent and record final verification in `specs/264-settings-operations-deployment-update-ui/verification.md`. (FR-001 through FR-012)

## Dependencies

T001-T003 precede T004-T008. T004-T007 can be authored in parallel. T008 follows red-first test creation. T009-T011 follow implementation.
