# Research: Secret-Safe Settings and Managed Secrets Workflows

## FR-001 / One-Way Plaintext Submission

Decision: Keep plaintext submission limited to existing create, update, and rotate request bodies and verify UI state clears after successful mutation.
Evidence: `frontend/src/components/secrets/SecretManager.tsx`, `api_service/api/routers/secrets.py`.
Rationale: The existing surface already uses password inputs and clears create/update state. The story does not require a new secret backend.
Alternatives considered: Introducing a separate Settings-specific secret create endpoint was rejected because the Secrets System remains authoritative.
Test implications: Frontend unit coverage.

## FR-002 / Metadata Includes SecretRef

Decision: Add a derived `secretRef` field to safe secret metadata responses.
Evidence: `api_service/api/schemas.py` currently returns slug, status, details, createdAt, and updatedAt, but no canonical reference.
Rationale: The UI should show and copy `db://<slug>` without deriving inconsistent references in multiple components.
Alternatives considered: Deriving only in React was rejected because API consumers also need canonical metadata.
Test implications: API unit coverage.

## FR-003 / Copy SecretRef UI

Decision: Add a row action in `SecretManager` that copies `db://<slug>` and reports success or failure without reading plaintext.
Evidence: `frontend/src/components/secrets/SecretManager.tsx` currently has Edit, Rotate, and Delete actions only.
Rationale: The brief explicitly requires showing metadata plus SecretRef after one-way submission.
Alternatives considered: Showing only static text was rejected because the source workflow includes copying SecretRefs.
Test implications: Vitest/Testing Library component test.

## FR-004 / Raw Secret Rejection

Decision: Preserve existing backend rejection of secret-shaped generic overrides.
Evidence: `tests/unit/api_service/api/routers/test_settings_api.py::test_secret_ref_reference_allowed_but_raw_secret_rejected`.
Rationale: Existing test evidence already covers this behavior.
Alternatives considered: Adding duplicate validation in the frontend was rejected because the backend is authoritative.
Test implications: Existing pytest plus final verification.

## FR-006 / Redacted Validation Diagnostics

Decision: Replace the bare validation response with a redacted diagnostic envelope containing `valid`, `status`, `checkedAt`, and non-secret diagnostics.
Evidence: `api_service/api/routers/secrets.py` currently returns only `{ "valid": bool }`.
Rationale: The story requires redacted diagnostics and a timestamp while preventing plaintext readback.
Alternatives considered: Returning provider-specific validation detail was rejected for this story because no provider context is supplied by the endpoint.
Test implications: API unit coverage for active and missing/inactive secrets, plus redaction assertions.

## FR-007 / `db://` SecretRef Diagnostics

Decision: Extend `SettingsCatalogService` to inspect managed-secret metadata for `db://` references when a session is available and report missing or inactive statuses.
Evidence: `api_service/services/settings_catalog.py` currently diagnoses missing `env://` values and syntactically invalid references, but not `db://` managed-secret status.
Rationale: Settings catalog/effective responses are where generic SecretRef settings surface broken-reference state.
Alternatives considered: Resolving plaintext through `SecretsService.get_secret` was rejected because diagnostics only need metadata/status and should not decrypt values.
Test implications: Settings API route test with active and disabled managed secret rows.
