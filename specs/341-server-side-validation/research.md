# Research: Server-Side Validation and Cross-Setting Policy Enforcement

## FR-001 / DESIGN-REQ-006 - Authoritative key, scope, and actor validation

Decision: partial; keep existing server-authoritative catalog and permission checks, then normalize their validation result shape.
Evidence: `api_service/services/settings_catalog.py` defines `SettingsRegistry`, `SettingsCatalogService.ensure_write_allowed()`, and `apply_overrides()`; `api_service/api/routers/settings.py` maps write permissions by scope before applying changes.
Rationale: The backend already rejects unknown keys, invalid scopes, read-only settings, and missing write permissions. The gap is consistency: errors are raised through mixed exceptions and route-specific responses rather than a shared validation result that carries key, scope, failed rule, and boundary.
Alternatives considered: Replace the settings service wholesale. Rejected because current registry and override persistence already provide the right authoritative boundary.
Test implications: unit + integration.

## FR-002 / SC-001 - Supported value type validation

Decision: partial; extend or explicitly formalize validation for every supported descriptor type named by MM-656.
Evidence: `_validate_override_value()` handles enum, integer, boolean, secret_ref, and string. Existing registry currently exposes these categories, but the spec requires booleans, strings, numbers, enums, lists, objects, and SecretRefs.
Rationale: The validator must either support list/object descriptors or fail fast for unsupported descriptor types with typed errors. Because the source design lists list/object settings as supported generic categories, planning assumes implementation should add coverage-ready validation support.
Alternatives considered: Mark list/object out of scope because current registry does not expose them. Rejected because MM-656 explicitly requires type validation for those categories.
Test implications: unit first, with integration coverage for API-visible categories.

## FR-003 / DESIGN-REQ-007 / DESIGN-REQ-008 - Constraint and unsafe object validation

Decision: partial; preserve existing size and unsafe-payload guards, then add complete descriptor constraint evaluation.
Evidence: `_MAX_OVERRIDE_VALUE_BYTES`, `_contains_unsafe_payload()`, enum options, and integer min/max checks exist in `SettingsCatalogService`. String length/pattern, list constraints, and object schema restrictions are not fully evaluated.
Rationale: The source design requires numeric/string/list/object constraints and forbids executable code/templates/commands unless a specialized subsystem owns them. Current unsafe token scanning is useful but not a complete descriptor-driven validator.
Alternatives considered: Rely on client-side constraints. Rejected because backend validation is authoritative.
Test implications: unit + integration for API error shape.

## FR-004 / SCN-003 - Referenced resource and SecretRef validation

Decision: partial; promote diagnostics into blocking validation where required by boundary.
Evidence: `_secret_ref_diagnostic()` reports invalid or unresolved env/db SecretRefs; `_provider_profile_ref_diagnostic()` reports missing or disabled provider profiles. `api_service/api/routers/provider_profiles.py` also validates provider profile SecretRef syntax for readiness.
Rationale: Diagnostics exist, but a write can still persist some references and surface diagnostics later. MM-656 requires validation boundaries to reject missing SecretRefs and broken provider profile bindings before unsafe use.
Alternatives considered: Keep diagnostics-only behavior. Rejected because the spec says invalid settings and missing references must fail explicitly without fallback.
Test implications: unit + integration.

## FR-005 / FR-006 / DESIGN-REQ-002 / DESIGN-REQ-004 - Workspace policy and cross-setting validation

Decision: missing; add a compact policy and cross-setting rule layer at the settings validation boundary.
Evidence: Current descriptors expose allowed enum values and canary min/max, but no shared workspace policy object or rule set was found for allowed runtime list, allowed providers, max canary, publication modes, SecretRef backend policy, maintenance-mode operation conflicts, or disabled-feature canary behavior.
Rationale: MM-656's highest-risk gap is validation across settings. This should not be implemented as scattered route checks; it belongs near `SettingsCatalogService` or a collaborator so write, preview, launch/operation, and diagnostics paths share rule semantics.
Alternatives considered: Store workspace policy as a new table. Rejected for first implementation because existing configuration, descriptors, and request context can provide deterministic policy inputs without new persistence.
Test implications: unit + integration.

## FR-007 / SCN-006 - Locked, unsupported, and ineligible setting errors

Decision: partial; preserve existing read-only and invalid-scope checks while normalizing structured errors.
Evidence: `ensure_write_allowed()` rejects read-only/operator-locked entries; router returns `read_only_setting`, `setting_not_exposed`, and `invalid_scope` responses. MM-655 tests cover operator-lock metadata.
Rationale: The behavior exists in pieces, but the story requires a consistent validator-owned result surface and no silent mutation.
Alternatives considered: Leave current route mapping unchanged. Rejected because downstream timing boundaries need a shared structured validation contract.
Test implications: unit + integration.

## FR-008 / DESIGN-REQ-005 / SC-004 - Validation timing boundaries

Decision: missing; define validation boundaries and route consumers through them.
Evidence: `apply_overrides()` validates before persistence. `catalog_async()`, `effective_value_async()`, and `diagnostics()` calculate diagnostics. No single boundary enum or shared validation path covers descriptor generation, write request, pre-persistence, effective-value preview, launch/operation execution, and readiness diagnostics.
Rationale: Timing consistency is explicitly required by section 18.3. A boundary-aware validation function avoids drift between write-time rejection and preview/diagnostic behavior.
Alternatives considered: Add tests only around existing functions. Rejected because existing functions do not represent every timing boundary.
Test implications: unit + integration.

## FR-009 - Structured validation error shape

Decision: partial; extend existing `SettingsError` and diagnostics conventions into a validator result/error contract.
Evidence: `SettingsError` has `error`, `message`, `key`, `scope`, and `details`; `SettingDiagnostic` has `code`, `message`, `severity`, and `details`. Some write errors currently omit failed key/rule/boundary because exceptions collapse to route-level `invalid_setting_value`.
Rationale: Keep the existing response idiom but ensure validation failures are machine-readable and actionable for each setting.
Alternatives considered: Introduce a separate API error format. Rejected because it would fragment the Settings API contract.
Test implications: unit + integration.

## FR-010 / DESIGN-REQ-001 / DESIGN-REQ-011 - Fail-fast and no sensitive fallback

Decision: partial; strengthen coverage and apply the rule across all validation boundaries.
Evidence: Current code rejects obvious raw token prefixes and unsafe payloads and reports unresolved SecretRefs/provider profiles. It does not prove no fallback across every boundary.
Rationale: The failure mode is security-sensitive. Tests should assert rejected writes/previews do not mutate values and diagnostics do not expose plaintext or route to another sensitive source.
Alternatives considered: Rely on existing resolver source metadata tests. Rejected because MM-656 requires fail-fast behavior, not only source explanation.
Test implications: unit + integration.

## FR-011 / SC-001 through SC-004 - Regression coverage

Decision: missing; add a focused MM-656 test matrix before implementation changes.
Evidence: Existing tests cover settings catalog snapshots, overrides, audit, diagnostics, and MM-655 effective-value behavior, but there is no complete MM-656 matrix for all value categories, cross-setting rules, timing boundaries, and fail-fast fallback.
Rationale: The implementation touches a shared control-plane contract, so tests should lead the change and prevent silent drift.
Alternatives considered: Only update existing tests opportunistically. Rejected because coverage must map to every MM-656 success criterion.
Test implications: unit + integration.

## FR-012 / SC-005 - Traceability

Decision: implemented_unverified; preserve MM-656 in every generated artifact and require final verification to check it.
Evidence: `specs/341-server-side-validation/spec.md` preserves the original Jira preset brief; this plan, research, data model, contract, and quickstart all reference MM-656.
Rationale: Traceability does not require production code but must survive downstream tasks and verification.
Alternatives considered: Rely on Jira issue metadata only. Rejected because final verification compares local artifacts.
Test implications: final verification; optional unit traceability guard if downstream tasks require it.

## Unit Test Strategy

Decision: Use focused pytest unit tests around `SettingsCatalogService`, any new validator collaborator, and settings router error mapping.
Evidence: Existing tests live in `tests/unit/services/test_settings_catalog.py` and `tests/unit/api_service/api/routers/test_settings_api.py`.
Rationale: Unit tests can exhaustively cover value categories, constraints, policy rules, boundary enum behavior, structured errors, and no-mutation guarantees without compose services.
Alternatives considered: Put all coverage in integration tests. Rejected because the rule matrix needs fast red-first feedback.
Test implications: unit.

## Integration Test Strategy

Decision: Use hermetic API integration tests for the Settings API write/effective/diagnostics boundaries.
Evidence: Existing integration contracts live in `tests/integration/api/test_settings_overrides_contract.py` and `tests/integration/api/test_settings_effective_values_contract.py` and are marked `integration_ci`.
Rationale: MM-656 must prove API-visible behavior and persistence/no-mutation semantics through the real FastAPI route and database fixtures.
Alternatives considered: Browser/UI tests. Rejected for this story unless implementation later adds UI behavior, because the acceptance criteria are server-side validation focused.
Test implications: integration_ci.
