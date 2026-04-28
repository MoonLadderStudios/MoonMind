# Tasks: Generated User and Workspace Settings UI

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/settings-user-workspace-ui.md](./contracts/settings-user-workspace-ui.md)

**Prerequisites**: Existing Settings API routes and descriptor service remain in place.

**Unit Test Command**: `npm run ui:test -- frontend/src/components/settings/GeneratedSettingsSection.test.tsx`

**Integration Test Command**: `./tools/test_unit.sh tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx`

**Source Traceability**: MM-539 and the canonical Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-011, SCN-001 through SCN-006, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-009, and DESIGN-REQ-023.

## Phase 1: Setup

- [X] T001 Create MoonSpec feature artifacts under `specs/269-generated-user-workspace-settings-ui/` preserving MM-539 and source design mappings.
- [X] T002 Confirm `.specify/feature.json` points to `specs/269-generated-user-workspace-settings-ui`.

## Phase 2: Foundational

- [X] T003 [P] Add frontend test file `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` with mocked Settings API catalog, save, and reset responses for FR-001 through FR-010.
- [X] T004 [P] Add generated settings component shell in `frontend/src/components/settings/GeneratedSettingsSection.tsx` with typed descriptor, response, and pending-change models for FR-001 and FR-002.

## Phase 3: Story - Generated User and Workspace Settings

**Story Summary**: As a Mission Control user, I can configure eligible user and workspace settings through generated controls so local-first configuration is discoverable without bespoke forms for every setting.

**Independent Test**: Load Settings -> User / Workspace with mocked workspace and user descriptors, edit representative descriptors, preview pending changes, save changed keys only, reset an override, and verify read-only and SecretRef rows remain safe.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-023.

- [X] T005 Add failing frontend test that renders workspace descriptors by category, source badge, scope badge, diagnostics, affected subsystems, and reload badges in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for FR-001, FR-003, SCN-001, SC-001, DESIGN-REQ-001, and DESIGN-REQ-004.
- [X] T006 Add failing frontend test that switches to user scope and fetches only user-scope descriptors in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for FR-004 and SCN-002.
- [X] T007 Add failing frontend test that edits enum, boolean, number, text, list, key/value, and SecretRef descriptors and previews only changed keys in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for FR-002, FR-005, FR-006, FR-009, SC-002, and SC-004.
- [X] T008 Add failing frontend test that save sends only changed keys with expected versions and refreshes catalog in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for FR-005, FR-010, and SC-002.
- [X] T009 Add failing frontend test that read-only descriptors show lock reasons and disable ordinary edits in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for FR-008, SCN-004, and SC-003.
- [X] T010 Add failing frontend test that reset-to-inherited calls the reset route only for override sources and refreshes catalog in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` for FR-007, SC-005, and DESIGN-REQ-023.
- [X] T011 Implement descriptor fetch, filters, category grouping, source/scope badges, diagnostics, and row metadata in `frontend/src/components/settings/GeneratedSettingsSection.tsx` for FR-001, FR-003, and FR-004.
- [X] T012 Implement generated controls for boolean, string, bounded number, enum, list, key/value, SecretRef, and read-only settings in `frontend/src/components/settings/GeneratedSettingsSection.tsx` for FR-002, FR-008, and FR-009.
- [X] T013 Implement pending change state, preview, discard, save request body, sanitized error display, and catalog refresh in `frontend/src/components/settings/GeneratedSettingsSection.tsx` for FR-005, FR-006, and FR-010.
- [X] T014 Implement reset-to-inherited behavior in `frontend/src/components/settings/GeneratedSettingsSection.tsx` for FR-007 and DESIGN-REQ-023.
- [X] T015 Replace the User / Workspace placeholder in `frontend/src/entrypoints/settings.tsx` with `GeneratedSettingsSection` while preserving profile display context for FR-001 and SCN-001.

## Phase 4: Validation

- [X] T016 Run `npm run ui:test -- frontend/src/components/settings/GeneratedSettingsSection.test.tsx` and record the result.
- [X] T017 Run `./tools/test_unit.sh tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx` and record the result.
- [X] T018 Run final `/moonspec-verify` work and preserve MM-539, source mappings, SC-006, and test evidence in final verification.

## Dependencies and Execution Order

1. T003 and T004 can run in parallel.
2. T005 through T010 define red-first coverage before production implementation.
3. T011 through T015 implement the story.
4. T016 through T018 validate the implementation and traceability.

## Implementation Strategy

Focus implementation on the frontend renderer because backend Settings API behavior already exists and is covered. Keep the generic renderer descriptor-driven; do not add setting-specific forms. Preserve backend authority by submitting changed values to the existing API and displaying backend-provided diagnostics and errors.
