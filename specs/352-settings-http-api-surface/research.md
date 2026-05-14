# Research: Settings HTTP API Surface

## FR-001 / DESIGN-REQ-001 - Catalog Section And Scope Reads

Decision: Treat catalog reads as implemented but add a contract-level proof for all three top-level sections.
Evidence: `api_service/api/routers/settings.py` defines `GET /settings/catalog`; `SettingsCatalogService.catalog()` and `catalog_async()` delegate to `SettingsCatalogBuilder`; `tests/unit/api_service/api/routers/test_settings_api.py` covers `section=user-workspace&scope=workspace`; `tests/unit/services/test_settings_catalog.py` covers builder section filtering.
Rationale: The route and service behavior exist, but MM-657 calls out the three sections together as acceptance evidence.
Alternatives considered: Rebuilding catalog grouping was rejected because existing registry/builder boundaries already own descriptor grouping.
Test implications: Add focused integration or API unit coverage for `providers-secrets`, `user-workspace`, and `operations`.

## FR-003 / FR-004 / DESIGN-REQ-002 - Effective Reads

Decision: Treat effective list and single-key reads as implemented and verified.
Evidence: `GET /settings/effective` and `GET /settings/effective/{key}` exist in `api_service/api/routers/settings.py`; `SettingsCatalogService.effective_values*()` and `effective_value*()` return source explanations, value versions, diagnostics, and reload/read-only metadata; `tests/integration/api/test_settings_effective_values_contract.py` verifies metadata and operator locks.
Rationale: Current behavior satisfies MM-657 effective-read scope and source requirements.
Alternatives considered: Adding separate resolver APIs was rejected because this story is about the documented settings HTTP surface.
Test implications: Final verification should rerun existing effective-value contract tests; no new implementation expected for these items.

## FR-005 / FR-006 / FR-007 / DESIGN-REQ-003 / DESIGN-REQ-011 - Updates And Version Conflicts

Decision: Treat user/workspace update behavior, optimistic concurrency, refreshed values, and audit metadata as implemented and verified.
Evidence: `PATCH /settings/{scope}` accepts `changes`, `expected_versions`, and `reason`; `SettingsCatalogService.apply_overrides()` validates, checks expected versions, persists atomically, records audit events, and returns changed effective values; unit and integration tests cover stale versions, invalid values, no mutation on errors, and audit output.
Rationale: Existing override and validation stories already delivered the core update contract.
Alternatives considered: Introducing a second update service was rejected because current service already owns catalog validation and persistence.
Test implications: Preserve existing unit/integration tests; add validate/preview tests separately.

## FR-008 / DESIGN-REQ-004 - Reset

Decision: Treat reset behavior as implemented and verified.
Evidence: `DELETE /settings/{scope}/{key}` exists; `SettingsCatalogService.reset_override()` deletes only the override row and returns `effective_value_async()`; tests cover reset preserving secrets and audit rows.
Rationale: This matches MM-657 reset semantics.
Alternatives considered: Returning descriptors instead of effective value was rejected because current reset contract and Jira brief both accept returning inherited effective state.
Test implications: Final verification only unless downstream implementation changes reset-adjacent code.

## FR-009 / FR-010 / DESIGN-REQ-005 - Validate And Preview Routes

Decision: Public validation and preview endpoints are missing and require test-first implementation.
Evidence: `api_service/api/routers/settings.py` has no `POST /settings/validate` or `POST /settings/preview`; `SettingsCatalogService` exposes `validate_effective_preview()`, `validate_launch_execution()`, `validate_operation_execution()`, and readiness diagnostics, but there is no public non-committing route response with effective-value diffs, dependency warnings, and reload requirements.
Rationale: MM-657 explicitly names these endpoints and their no-commit behavior.
Alternatives considered: Reusing `GET /settings/diagnostics` alone was rejected because diagnostics cannot evaluate arbitrary proposed changes or return proposed diffs.
Test implications: Add unit route tests and hermetic integration tests that submit proposed changes, assert no persistence, and verify validation details/diffs/reload metadata.

## FR-011 / FR-012 / DESIGN-REQ-006 / DESIGN-REQ-009 - Audit Reads And Redaction

Decision: Treat audit reads and redaction as implemented and verified.
Evidence: `GET /settings/audit` supports key/scope filters and limit; `SettingsCatalogService.list_audit_events()` applies workspace/user scoping and redaction policy; unit tests cover redaction without metadata permission, exposing SecretRef metadata with permission, workspace/user scoping, apply mode, affected systems, and secret-like value blocking.
Rationale: Existing audit diagnostics work matches MM-657 audit requirements.
Alternatives considered: Adding a separate audit endpoint for MM-657 was rejected to avoid duplicate public contracts.
Test implications: Existing audit tests remain required; new validate/preview work must not weaken audit output.

## FR-013 / DESIGN-REQ-007 - Structured Error Matrix

Decision: Treat the shared error envelope as partial and add coverage for the MM-657 route matrix.
Evidence: `SettingsError` and `settings_error()` exist; router tests cover `unknown_setting`, `setting_not_exposed`, `invalid_scope`, `read_only_setting`, `invalid_setting_value`, `version_conflict`, and `permission_denied`; MM-657 also names `scope_not_allowed`, `operator_locked`, `secret_ref_not_resolvable`, `provider_profile_not_found`, and `requires_confirmation`.
Rationale: Some named concepts are represented by existing canonical codes or validation details, but validate/preview routes need explicit contract decisions and tests.
Alternatives considered: Adding aliases for every Jira wording was rejected because the pre-release compatibility policy favors one clean contract; unsupported names should be consciously mapped or rejected.
Test implications: Add a documented error matrix for public routes and assert envelope fields for each supported code.

## FR-014 - Authorization

Decision: Treat existing route authorization as implemented but require tests for new validate/preview routes.
Evidence: Catalog/effective/audit/write routes call `_require_permission()` with settings permissions; tests cover permission denied for catalog/write and audit metadata behavior.
Rationale: The current pattern is established and should be reused.
Alternatives considered: Deferring validate/preview authorization to service helpers was rejected because backend route authorization is the product boundary.
Test implications: Add permission-denied tests for validate and preview.

## FR-015 / FR-016 / DESIGN-REQ-010 - Secret Hygiene And Broken References

Decision: Treat secret plaintext protection as implemented and broken-reference diagnostics as partial for the validate/preview surface.
Evidence: Existing tests cover SecretRef redaction, missing/disabled managed secrets, missing provider profiles, unsafe payloads, and provider-profile diagnostics; no preview route currently exercises arbitrary proposed broken references.
Rationale: Validate/preview must preserve these guarantees for proposed changes.
Alternatives considered: Trusting write-route tests alone was rejected because preview accepts non-committed values and is a separate attack surface.
Test implications: Add validate/preview cases for missing SecretRefs, missing provider profiles, policy-blocked values, and redaction.

## Setup Script Gap

Decision: Continue planning from `.specify/feature.json` because the setup helper path is absent.
Evidence: `scripts/bash/setup-plan.sh --json` failed with `No such file or directory`; `.specify/feature.json` points to `specs/352-settings-http-api-surface`.
Rationale: The active feature directory is unambiguous and existing feature artifacts are present.
Alternatives considered: Stopping was rejected because the missing helper is not needed to produce the requested design artifacts.
Test implications: None beyond reporting the gap.
