# Research: Generated User and Workspace Settings UI

## FR-001 Descriptor Source

Decision: Use the existing `GET /api/v1/settings/catalog?section=user-workspace&scope=<scope>` route as the sole descriptor source.
Evidence: `api_service/api/routers/settings.py` exposes catalog filtering; `api_service/services/settings_catalog.py` builds descriptor metadata from explicit registry entries.
Rationale: The backend is already the authority for eligibility and section/scope filtering, matching the MM-539 requirement that frontend not decide eligibility.
Alternatives considered: Hardcoded frontend field list was rejected because it would recreate bespoke forms and drift from backend metadata.
Test implications: Frontend unit tests should assert scoped catalog fetches and rendered descriptor data.

## FR-002 Control Coverage

Decision: Implement a generated renderer for `toggle`, `select`, `number`, `text`, `secret_ref_picker`, list, key/value, and read-only descriptors.
Evidence: `SettingDescriptor` contains `type`, `ui`, `options`, `constraints`, `read_only`, and `sensitive` metadata.
Rationale: Current registry covers enum, integer, boolean, and SecretRef; the story requires the generic renderer to support documented descriptor shapes as they are added.
Alternatives considered: Implementing only current registry shapes was rejected because the Jira brief explicitly requires supported generic shapes.
Test implications: Frontend unit tests should provide mock descriptors for all required UI shapes.

## FR-005 Save Boundary

Decision: Submit only pending changed keys to `PATCH /api/v1/settings/{scope}` with descriptor `value_version` as expected version.
Evidence: `SettingsPatchRequest` accepts `changes` and `expected_versions`; service enforces version conflict and server-side validation.
Rationale: This preserves local user intent while keeping validation authoritative on the backend.
Alternatives considered: Sending the whole catalog was rejected because it would overwrite values the user did not intend to change.
Test implications: Test request body contains only changed keys.

## FR-007 Reset Boundary

Decision: Use `DELETE /api/v1/settings/{scope}/{key}` only when descriptor source is `workspace_override` or `user_override`.
Evidence: `reset_setting` route exists and `SettingsCatalogService.reset_override` returns effective inherited value after deleting override.
Rationale: Reset-to-inherited should be visible only when there is an override to remove.
Alternatives considered: Client-side null patch was rejected because reset semantics are already explicit in the API.
Test implications: Test reset calls the encoded key URL and refreshes catalog.

## FR-009 SecretRef Safety

Decision: Treat `type: secret_ref` or `ui: secret_ref_picker` as a reference field accepting SecretRef strings only, with helper text that never asks for plaintext.
Evidence: Backend validation rejects raw secret-like values and redacts unsafe values; docs require SecretRef pickers and no plaintext readback.
Rationale: The generic settings UI must not become a secret editor.
Alternatives considered: Listing managed secret metadata inside this component was deferred because Providers & Secrets already owns the specialized secret manager.
Test implications: Test SecretRef labels and absence of plaintext-oriented language.

## Existing Backend Coverage

Decision: Reuse existing backend API/service tests and add frontend tests for missing UI behavior.
Evidence: `tests/unit/api_service/api/routers/test_settings_api.py` covers catalog, effective values, patch, reset, invalid scopes, errors, and secret preservation; `tests/unit/services/test_settings_catalog.py` covers descriptor metadata, env parsing, SecretRef diagnostics, override persistence, inheritance, and conflicts.
Rationale: The backend is already materially covered; the implementation gap is the generated frontend renderer.
Alternatives considered: Adding duplicate backend tests was rejected unless code changes backend contracts.
Test implications: Run focused frontend tests plus relevant existing backend tests.
