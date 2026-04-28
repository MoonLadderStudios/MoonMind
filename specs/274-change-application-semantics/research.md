# Research: Change Application, Reload, Restart, and Recovery Semantics

## FR-001 / DESIGN-REQ-016 Descriptor Validation

Decision: Partial existing behavior; add descriptor-generation validation for apply semantics.
Evidence: `api_service/services/settings_catalog.py` defines `SettingRegistryEntry`, `SettingDescriptor`, and `SettingsCatalogService.catalog()`, but registry entries currently default reload/restart booleans and do not require explicit apply-mode semantics.
Rationale: MM-544 requires every setting to declare how changes apply. Descriptor generation is the earliest boundary to catch incomplete metadata.
Alternatives considered: Relying on UI badges only was rejected because it does not validate backend-owned descriptors.
Test implications: Unit tests for registry validation and catalog generation.

## FR-002 / FR-012 Write, Preview, Launch, and Diagnostic Validation

Decision: Partial existing behavior; extend tests and service outputs to cover late validation visibility.
Evidence: `SettingsCatalogService.apply_overrides()` validates keys, scopes, read-only state, value type, expected version, and unsafe SecretRef/plaintext cases. `/api/v1/settings/diagnostics` exposes actionable diagnostics for missing references.
Rationale: Existing write validation is strong, but MM-544 requires evidence across multiple timing boundaries, including post-persistence preview/readiness diagnostics.
Alternatives considered: Adding a separate validation endpoint was rejected unless existing diagnostics/effective responses cannot carry the evidence.
Test implications: Unit and API tests for write response diagnostics and diagnostics endpoint after reference changes.

## FR-003 / FR-007 Apply Mode Model

Decision: Missing as a first-class model; add explicit apply-mode metadata to descriptors and effective diagnostics.
Evidence: `SettingDescriptor` includes `requires_reload`, `requires_worker_restart`, `requires_process_restart`, and `applies_to`, while `SettingsAuditRead` has `apply_mode`; registry entries do not expose `apply_mode`.
Rationale: Booleans are insufficient to distinguish immediate, next request/task/launch, reload, restart, and manual operation.
Alternatives considered: Inferring apply mode from booleans was rejected because it would be ambiguous for next-task and manual-operation semantics.
Test implications: Unit, API, and UI tests for apply-mode serialization and display.

## FR-004 / SC-002 Structured Change Events

Decision: Partial existing behavior; populate apply mode and affected systems on persisted settings events.
Evidence: `SettingsAuditEvent` is read via `SettingsAuditRead` with `apply_mode` and `affected_systems`, and `apply_overrides()` writes audit rows through `_audit_event()`.
Rationale: The current audit mechanism is the closest durable local event surface; it should carry the MM-544 structured event fields.
Alternatives considered: Adding a new table was rejected because no new persistent storage is needed unless the existing audit event cannot support required fields.
Test implications: Unit and API audit tests asserting event type, key, scope, actor, timestamp, apply mode, and affected systems.

## FR-005 Consumer Refresh and Reload

Decision: Partial; use observable settings events and diagnostics to drive consumer refresh/reload outcomes.
Evidence: The UI invalidates the catalog query after settings saves, settings descriptors include `applies_to`, and operations settings already have status-backed tests.
Rationale: The story requires consumers to observe change effects, not necessarily to introduce a new pub/sub system if existing request/diagnostic surfaces can prove behavior.
Alternatives considered: Implementing a broad event bus was rejected as unnecessary for the single story unless tests reveal no existing consumer boundary can observe changes.
Test implications: Integration or API tests for catalog refresh/task default visibility and operations status where applicable.

## FR-006 / SC-004 Restart Visibility

Decision: Partial; add a richer activation state read model and UI presentation.
Evidence: `SettingsDiagnosticRead` exposes restart booleans and `GeneratedSettingsSection.tsx` renders reload/restart badges, but it does not show pending value, active state, affected process/worker, or completion guidance.
Rationale: Operators need actionable restart state, not only a badge.
Alternatives considered: Encoding guidance only as free-form diagnostic messages was rejected because tests and UI need structured fields.
Test implications: API and Vitest coverage for activation state and guidance.

## FR-009 / FR-010 / DESIGN-REQ-025 Backup Safety

Decision: Implemented unverified to partial; add story-specific tests around backup-safe data and restored-reference diagnostics.
Evidence: Existing settings tests reject raw secret-like values, redacts sensitive audit values, and surfaces unresolved SecretRef/provider profile diagnostics without plaintext. No explicit settings backup/export surface was found during planning.
Rationale: MM-544 can be satisfied initially by ensuring settings-visible data that would be backed up contains references and metadata only, while restored missing references are surfaced.
Alternatives considered: Building a full backup subsystem was rejected as outside this single settings semantics story.
Test implications: Unit/API tests for sanitized settings rows, audit output, and missing restored references.

## FR-011 Restored Reference Diagnostics

Decision: Partial; extend supported diagnostics for missing secrets, provider profiles, and OAuth/session references where the current model supports them.
Evidence: `SettingsCatalogService` already emits `unresolved_secret_ref`, `provider_profile_not_found`, and `provider_profile_disabled` diagnostics.
Rationale: The story calls out restore gaps for secrets, OAuth volumes, and provider profiles; existing reference diagnostics cover two of those categories and should be made explicit and traceable.
Alternatives considered: Treating restore gaps as generic validation errors was rejected because operators need clear reference-specific remediation.
Test implications: Unit and API diagnostics tests.

## Frontend Contract

Decision: Extend `GeneratedSettingsSection` rather than creating a separate settings page.
Evidence: The component already renders descriptor metadata, badges, diagnostics, pending local changes, save/reset, and tests under `GeneratedSettingsSection.test.tsx`.
Rationale: MM-544 is about making existing settings behavior visible, so the existing generated settings UI is the right boundary.
Alternatives considered: A standalone activation panel was rejected because it would split the operator workflow.
Test implications: Vitest tests for apply mode, pending activation, affected subsystem, and broken reference messaging.
