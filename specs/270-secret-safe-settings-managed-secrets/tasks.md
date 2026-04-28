# Tasks: Secret-Safe Settings and Managed Secrets Workflows

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/secret-safe-settings-contract.md](./contracts/secret-safe-settings-contract.md)

**Prerequisites**: Existing Settings API, Secrets API, Managed Secrets service, and Settings UI remain in place.

**Unit Test Command**: `pytest tests/unit/api/test_secrets_api.py tests/unit/services/test_secrets.py tests/unit/api_service/api/routers/test_settings_api.py -q`

**Integration Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_secrets_api.py tests/unit/services/test_secrets.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py --ui-args frontend/src/components/secrets/SecretManager.test.tsx`

**Source Traceability**: MM-540 and the canonical Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-018.

## Phase 1: Setup

- [X] T001 Create MoonSpec feature artifacts under `specs/270-secret-safe-settings-managed-secrets/` preserving MM-540 and source design mappings. (FR-010, SC-005)
- [X] T002 Update `.specify/feature.json` to point at `specs/270-secret-safe-settings-managed-secrets`. (FR-010)

## Phase 2: Foundational

- [X] T003 Add failing API tests for `secretRef` metadata and redacted validation diagnostics in `tests/unit/api/test_secrets_api.py`. (FR-002, FR-006, SC-001, DESIGN-REQ-011, DESIGN-REQ-018)
- [X] T004 Add failing service tests for metadata-only validation results in `tests/unit/services/test_secrets.py`. (FR-006, SC-001)
- [X] T005 Add failing Settings API test for active, disabled, and missing `db://` SecretRef diagnostics in `tests/unit/api_service/api/routers/test_settings_api.py`. (FR-005, FR-007, SC-004, DESIGN-REQ-010)
- [X] T006 Add failing frontend test for Managed Secrets copyable `db://` refs and plaintext suppression in `frontend/src/components/secrets/SecretManager.test.tsx`. (FR-001, FR-002, FR-003, SC-002)

## Phase 3: Story - Create and Bind SecretRefs Safely

**Story Summary**: Secret managers and workspace admins can create managed secrets from Settings, copy and bind SecretRefs, and receive redacted validation/broken-reference diagnostics without plaintext readback.

**Independent Test**: Create/list/validate managed secrets, copy `db://<slug>` in the UI, bind the reference to generic Settings, and verify raw secrets are rejected while missing/inactive refs are diagnosed.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-018.

- [X] T007 Add `secretRef` to safe secret metadata response models in `api_service/api/schemas.py`. (FR-002, SC-001)
- [X] T008 Implement redacted managed-secret validation result helpers in `api_service/services/secrets.py`. (FR-006, DESIGN-REQ-011)
- [X] T009 Update `GET /api/v1/secrets/{slug}/validate` in `api_service/api/routers/secrets.py` to return redacted diagnostics. (FR-006, SC-001)
- [X] T010 Implement `db://` managed-secret status diagnostics in `api_service/services/settings_catalog.py`. (FR-005, FR-007, SC-004)
- [X] T011 Add Managed Secrets UI SecretRef display and copy action in `frontend/src/components/secrets/SecretManager.tsx`. (FR-001, FR-002, FR-003, SC-002)

## Phase 4: Validation

- [X] T012 Run `pytest tests/unit/api/test_secrets_api.py tests/unit/services/test_secrets.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py -q` and record the result. (SC-001, SC-003, SC-004)
- [X] T013 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/components/secrets/SecretManager.test.tsx` and record the result. (SC-002)
- [X] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_secrets_api.py tests/unit/services/test_secrets.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py --ui-args frontend/src/components/secrets/SecretManager.test.tsx` or record exact blocker. (SC-001, SC-002, SC-003, SC-004)
- [X] T015 Run final `/moonspec-verify` work and preserve MM-540, source mappings, and test evidence in `specs/270-secret-safe-settings-managed-secrets/verification.md`. (FR-010, SC-005)

## Dependencies and Execution Order

1. T003 through T006 define red-first coverage before production implementation.
2. T007 through T011 implement the story.
3. T012 through T015 validate implementation and traceability.

## Implementation Strategy

Keep the change narrowly scoped to existing Secrets and Settings boundaries. Add response metadata and diagnostics without changing encryption, resolver ownership, or provider-profile semantics. Preserve backend authority for generic settings validation and keep UI behavior reference-only.
