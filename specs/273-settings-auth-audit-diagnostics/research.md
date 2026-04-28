# Research: Settings Authorization Audit Diagnostics

## FR-001 / DESIGN-REQ-014 Permission Taxonomy

Decision: Add explicit permission constants and backend action-to-permission mapping for settings catalog/effective reads, writes, secret metadata/value operations, secret lifecycle operations, provider profile reads/writes, operations read/invoke, and audit reads.
Evidence: `api_service/api/routers/settings.py` currently exposes settings reads/writes without a settings-specific permission dependency; `api_service/services/settings_catalog.py` has scopes and sensitive descriptors but no permission model.
Rationale: The story requires narrow least-privilege categories independent of frontend visibility.
Alternatives considered: Reusing superuser-only checks was rejected because it cannot distinguish the required permission categories.
Test implications: Unit tests for mapping and integration tests for allowed/denied route calls.

## FR-003 through FR-007 / DESIGN-REQ-015 Audit Output

Decision: Keep existing `settings_audit_events` as the durable source and add a bounded audit query/output model with redaction logic at the service/API boundary. Add schema fields only where required for validation outcome, apply mode, and affected systems.
Evidence: `api_service/db/models.py` already defines `SettingsAuditEvent`; `api_service/services/settings_catalog.py` writes rows on override update/reset and tracks entry audit redaction policy.
Rationale: Reusing the existing audit table avoids a second audit source while satisfying operator-visible audit reads.
Alternatives considered: Returning raw audit rows was rejected because it would expose values without caller-aware redaction and lacks a stable contract.
Test implications: Unit tests for redaction, integration tests for `/settings/audit` permission and output.

## FR-008 through FR-010 / DESIGN-REQ-018 Diagnostics

Decision: Extend existing effective-value diagnostics rather than create a separate diagnostics store. Diagnostics should include source explanations, read-only reasons, validation failures, restart requirements, recent audit context, SecretRef/profile readiness blockers, and explicit no-fallback behavior.
Evidence: `SettingDiagnostic`, `_diagnostics`, `_secret_ref_diagnostic`, and `_provider_profile_ref_diagnostic` already exist in `api_service/services/settings_catalog.py`.
Rationale: Current effective settings responses already carry diagnostics; adding a focused diagnostics route/output preserves the existing model and makes test coverage direct.
Alternatives considered: A separate diagnostics subsystem was rejected as unnecessary for one settings story.
Test implications: Unit tests for service diagnostics and API tests for route output.

## FR-011 / FR-012 / DESIGN-REQ-025 Backend Authority

Decision: Route handlers must enforce permissions server-side and ignore any client-supplied descriptor or authorization metadata in patch payloads.
Evidence: `SettingsPatchRequest` accepts `changes`, `expected_versions`, and `reason`; unknown fields are currently ignored by default Pydantic behavior.
Rationale: Hidden frontend controls are a UX convenience only; backend tests must prove direct requests cannot bypass policy.
Alternatives considered: Frontend-only hiding was rejected by the source design and acceptance criteria.
Test implications: Integration tests submit direct backend requests without required permissions and with malicious descriptor metadata.

## Test Strategy

Decision: Add focused pytest coverage to existing settings service and API test modules, then run targeted tests followed by `./tools/test_unit.sh` when feasible.
Evidence: Existing settings tests already use SQLite-backed SQLAlchemy fixtures and ASGI `AsyncClient`.
Rationale: This story primarily changes backend service/API behavior and can be verified hermetically in unit tests.
Alternatives considered: Browser-only e2e tests were rejected because backend authorization and audit redaction are the critical security boundary.
Test implications: Required unit command is `./tools/test_unit.sh`; targeted iteration can use pytest paths directly.
