# Research: Provider Profile Management and Readiness in Settings

## FR-001 Provider Profile Workflows

Decision: Partial implementation exists; preserve existing CRUD/default workflows and strengthen readiness visibility.
Evidence: `api_service/api/routers/provider_profiles.py`; `frontend/src/components/settings/ProviderProfilesManager.tsx`; `tests/unit/api_service/api/routers/test_provider_profiles.py`.
Rationale: Existing endpoints cover list, get, create, update, delete, enable/disable through PATCH, default normalization, owner authorization, OAuth actions, and manual Claude API-key enrollment.
Alternatives considered: Create a separate Settings provider-profile API. Rejected because the existing router is already the public surface.
Test implications: Add unit/UI tests only for missing readiness/display behavior; keep existing CRUD tests.

## FR-002 and FR-003 Provider Profile Metadata Display

Decision: Partial implementation; the API carries most fields, while the table needs denser launch-relevant display.
Evidence: `ProviderProfileResponse` includes default model, model overrides, credential/materialization, OAuth fields, tags, priority, concurrency/cooldown; UI form exposes most but table omits several.
Rationale: The story is best completed by rendering existing data plus readiness rather than introducing new persistence.
Alternatives considered: Add backend descriptors for provider profile forms. Rejected for this story because the existing specialized UI already matches Provider Profiles as first-class resources.
Test implications: UI tests for model overrides, OAuth metadata, concurrency/cooldown, tags/priority, and readiness summary.

## FR-004 SecretRef Role Bindings

Decision: Implemented but under-verified. Syntax validation exists; add role-aware UI copy and tests.
Evidence: `validate_secret_refs_helper`; `parseSecretRefs`; Secret refs table summary.
Rationale: SecretRefs are role-keyed JSON object entries. The UI should make that role/value relationship explicit without displaying plaintext.
Alternatives considered: Replace JSON textarea with a full picker in this story. Rejected as too large and overlapping MM-540 managed secret picker work.
Test implications: UI test for role labels and SecretRef-only display; backend test for invalid raw value already exists.

## FR-005 Readiness Contract

Decision: Missing for general provider profiles; add a compact synthesized readiness object to profile responses.
Evidence: Claude auth readiness is embedded under `command_behavior.auth_readiness`, but generic rows have only `enabled`, `max_parallel_runs`, `cooldown_after_429_seconds`, SecretRefs, and OAuth fields.
Rationale: A normalized readiness field lets the API and UI explain launch blockers without moving launch behavior into Settings.
Alternatives considered: Query live ProviderProfileManager workflow state for every profile. Rejected for this story because it would add Temporal coupling and unstable latency to Settings list rendering.
Test implications: Backend unit tests for disabled, missing SecretRef, missing OAuth metadata, Claude provider readiness, and redaction; UI tests for display.

## FR-006 Provider Profile Reference Diagnostics

Decision: Add a narrow `workflow.default_provider_profile_ref` setting that stores only a provider profile identifier and emits launch-blocker diagnostics when the referenced profile is missing or disabled.
Evidence: `api_service/services/settings_catalog.py`; `tests/unit/services/test_settings_catalog.py`; `tests/unit/api_service/api/routers/test_settings_api.py`.
Rationale: The spec requires user/workspace settings to reference provider profiles without inlining provider-profile launch semantics. A typed reference setting plus diagnostics satisfies the requirement while keeping runtime construction, credentials, environment shaping, generated files, process launch, and capability checks outside generic settings.
Alternatives considered: Leave provider-profile references out of the settings catalog. Rejected during alignment because FR-006 and SC-004 explicitly require missing/disabled references to produce effective-setting diagnostics and launch blockers.
Test implications: Unit tests for catalog exposure, missing/disabled provider profile diagnostics, and user-scope effective settings shape.

## FR-007 and FR-008 Runtime Boundary

Decision: Implemented_unverified; preserve existing boundary and add no launch logic to Settings.
Evidence: `api_service/services/provider_profile_service.py` builds manager payloads; runtime strategy code lives under `moonmind/workflows`.
Rationale: Settings should edit metadata and display readiness, while the manager and runtime strategies launch agents.
Alternatives considered: Move command behavior validation into generic settings. Rejected by source design and security boundaries.
Test implications: Final verification and focused tests ensure readiness is diagnostic-only.

## FR-009 Sanitized Diagnostics

Decision: Partial; response redaction exists but readiness messages need explicit redaction coverage.
Evidence: `redact_sensitive_payload`, `redact_profile_file_templates`, existing redaction tests.
Rationale: Readiness may include provider validation failure text. It must not echo tokens.
Alternatives considered: Drop provider validation messages entirely. Rejected because actionable diagnostics are required; sanitized messages are sufficient.
Test implications: Unit and UI tests assert token-like text is redacted from readiness diagnostics.
