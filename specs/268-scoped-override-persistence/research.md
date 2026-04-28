# Research: Scoped Override Persistence and Inheritance

## FR-001 / DESIGN-REQ-006 - Inheritance Resolution

Decision: Extend the existing `SettingsCatalogService` to resolve values from config/defaults, workspace overrides, and allowed user overrides in that order.
Evidence: `api_service/services/settings_catalog.py` currently resolves environment/config/default only and returns `config_or_default`, `environment`, `default`, or `missing`.
Rationale: This preserves the read-side contract from `MM-537` while adding the sparse persisted layers required by `MM-538`.
Alternatives considered: A separate resolver service was rejected for this story because the existing catalog service already owns descriptor and effective-value resolution.
Test implications: Unit and API tests for no override, workspace override, user override, and intentional null override.

## FR-002 / FR-003 / FR-006 / DESIGN-REQ-017 - Override Storage

Decision: Add SQLAlchemy models and an Alembic migration for `settings_overrides` and `settings_audit_events` with unique scope/subject/key identity.
Evidence: No current `settings_overrides` or settings audit storage exists; writes return `settings_write_unavailable`.
Rationale: The source design requires durable sparse rows, version metadata, and reset-by-delete semantics.
Alternatives considered: In-memory overrides were rejected because they would not satisfy persistence or reset guarantees.
Test implications: Unit tests use an isolated async SQLite database with the SQLAlchemy models.

## FR-004 / FR-009 - Reset Preservation

Decision: Implement reset as a targeted delete of the matching override row plus audit event creation, returning the inherited effective value.
Evidence: Existing provider profile and managed secret models are separate tables; reset does not need to touch them.
Rationale: Resetting settings must not cascade into defaults, provider profiles, managed secrets, OAuth volumes, or audit history.
Alternatives considered: Setting override value to null on reset was rejected because the design defines reset as deleting the override row and because null can be an intentional override value.
Test implications: API reset tests check override deletion, inherited effective response, managed secret row preservation, and audit row preservation.

## FR-007 - Version Conflicts And Atomicity

Decision: Check every expected version before persisting a batch; if any mismatch occurs, return `version_conflict` and commit no changes.
Evidence: Existing `SettingsPatchRequest` already includes `expected_versions`, but the route does not use them.
Rationale: The Jira brief explicitly requires no partial change on conflicting expected versions.
Alternatives considered: Per-key partial success was rejected because it violates the acceptance criterion.
Test implications: Unit/API tests verify stale expected versions return `version_conflict` and leave all current override values unchanged.

## FR-008 / FR-010 / DESIGN-REQ-026 - Safe Value Validation

Decision: Validate override values against descriptor type/options/constraints and reject secret-like plaintext or unsafe structured payload markers. Allow SecretRef/resource reference strings as references only.
Evidence: `SettingsCatalogService` already has non-persistent SecretRef diagnostics and invalid SecretRef redaction for environment values.
Rationale: Override rows may store safe JSON and references but must never become a dumping ground for raw credentials, OAuth state, large artifacts, workflow payloads, or operational history.
Alternatives considered: Accepting arbitrary JSON for object values was rejected for this story because the current registry contains no generic object settings and the security requirement is explicit.
Test implications: Unit/API tests reject raw token-like strings for SecretRef settings and unsafe structured payload keys, while accepting `env://` or `db://` SecretRef references.

## FR-011 - Structured Errors

Decision: Reuse the existing `SettingsError` model and add write/reset error codes for `version_conflict`, `invalid_setting_value`, `read_only_setting`, `invalid_scope`, and `setting_not_exposed`.
Evidence: `api_service/api/routers/settings.py` already returns structured errors for invalid reads and unsupported writes.
Rationale: Maintaining one error shape keeps UI/API consumers stable.
Alternatives considered: Raising HTTP exceptions with ad hoc detail payloads was rejected because it would diverge from `MM-537`.
Test implications: API tests assert stable error shape and sanitized response content.
