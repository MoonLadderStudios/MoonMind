# Research: Sparse Settings Override Persistence and Reset

## FR-001 / Sparse Inheritance

Decision: `implemented_verified`; absence of an override resolves to inherited configured/default values without creating an override row.
Evidence: `api_service/services/settings_catalog.py` `_resolve_value_async`; `tests/unit/services/test_settings_catalog.py::test_workspace_override_persists_and_reports_version`.
Rationale: The resolver checks persisted overrides first and falls back to configured/default resolution with version `1` when none exists.
Alternatives considered: Add new inheritance logic for `MM-654`; rejected because current implementation already matches the spec.
Test implications: none beyond final verify.

## FR-002 / Workspace Overrides

Decision: `implemented_verified`; workspace overrides persist independently by key and workspace subject.
Evidence: `api_service/db/models.py` `SettingsOverride`; `api_service/services/settings_catalog.py::apply_overrides`; `tests/unit/api_service/api/routers/test_settings_api.py::test_patch_workspace_setting_persists_override`.
Rationale: The model stores scope, workspace ID, user ID, key, value, and version metadata with a uniqueness constraint.
Alternatives considered: New storage model; rejected because existing storage matches the story.
Test implications: none beyond final verify.

## FR-003 / User Overrides

Decision: `implemented_verified`; user overrides persist independently and win over workspace inheritance when eligible.
Evidence: `tests/unit/services/test_settings_catalog.py::test_user_override_wins_and_null_override_is_intentional`; `tests/unit/api_service/api/routers/test_settings_api.py::test_patch_user_setting_wins_over_workspace_inheritance`.
Rationale: Current resolver checks user override before workspace override for user scope.
Alternatives considered: Add separate user override store; rejected because existing scoped rows already distinguish user/workspace subjects.
Test implications: none beyond final verify.

## FR-004, FR-005, FR-009 / Reset-By-Delete Preservation

Decision: `implemented_verified`; reset deletes only the override and returns inherited effective value while preserving adjacent resources.
Evidence: `api_service/services/settings_catalog.py::reset_override`; `tests/unit/services/test_settings_catalog.py::test_reset_deletes_only_override_and_preserves_secret_and_audit`; `tests/unit/api_service/api/routers/test_settings_api.py::test_delete_reset_preserves_secret_and_audit`.
Rationale: The reset path deletes the specific override row, writes an audit event, commits, and re-reads the effective value.
Alternatives considered: Soft-delete override rows; rejected because source design requires sparse reset-by-delete semantics.
Test implications: none beyond final verify.

## FR-006 / Effective Source Explainability

Decision: `implemented_verified`; effective reads report inherited, workspace override, user override, migrated override, and intentional null states.
Evidence: `EffectiveSettingValue.source`; `source_explanation`; service tests for environment/default, workspace, user, and null override cases.
Rationale: The spec requires source visibility, and the current model exposes source and diagnostics on effective values.
Alternatives considered: Add separate explanation endpoint; not needed for this story.
Test implications: none beyond final verify.

## FR-007, FR-010 / Version Metadata And Atomic Concurrency

Decision: `implemented_verified`; override rows maintain `value_version`, and stale expected versions fail atomically.
Evidence: `SettingsOverride.value_version`; `apply_overrides` expected-version checks; `tests/unit/services/test_settings_catalog.py::test_version_conflict_is_atomic`; `tests/unit/api_service/api/routers/test_settings_api.py::test_version_conflict_returns_error_and_does_not_partially_persist`.
Rationale: Existing tests cover both service and API behavior for stale writes and no partial persistence.
Alternatives considered: Add row-level compatibility fallback; rejected by the pre-release compatibility policy and because existing behavior is explicit.
Test implications: none beyond final verify.

## FR-008, FR-011, SC-004, DESIGN-REQ-006 / Value Safety And Size Limits

Decision: `partial`; current validation rejects raw secret-looking values, invalid SecretRefs, wrong scalar types, and unsafe nested payload keys, but explicit serialized size limits and full fixture coverage for every disallowed payload class are missing.
Evidence: `api_service/services/settings_catalog.py::_validate_override_value`; `_contains_unsafe_payload`; `tests/unit/services/test_settings_catalog.py::test_unsafe_values_rejected_but_secret_refs_are_allowed`; `tests/unit/api_service/api/routers/test_settings_api.py::test_secret_ref_reference_allowed_but_raw_secret_rejected`.
Rationale: `MM-654` explicitly requires size limits and fixture proof for raw secrets, OAuth session blobs, decrypted credentials, generated config containing secrets, large artifacts, workflow payloads, and operational command history. The current tests cover only a subset.
Alternatives considered: Treat prior `MM-538` tests as sufficient; rejected because the Jira brief names additional validation categories and size limits that need direct evidence.
Test implications: add unit and API tests first; harden validation if any fixture is accepted.

## FR-012 / Verification Evidence

Decision: `partial`; existing focused tests cover most behavior, but final `MM-654` verification evidence has not been produced.
Evidence: prior service/API tests; `specs/339-sparse-settings-overrides/spec.md`.
Rationale: Verification evidence is a deliverable, not just code presence. It must explicitly cite `MM-654`.
Alternatives considered: Mark verified based on prior `MM-538` verification; rejected because issue traceability differs.
Test implications: run focused tests, then full unit suite when implementation tasks complete; final verification should summarize results.

## FR-013 / Traceability

Decision: `implemented_unverified`; `spec.md`, `plan.md`, and this research preserve `MM-654`, but downstream artifacts do not exist yet.
Evidence: `specs/339-sparse-settings-overrides/spec.md`; `specs/339-sparse-settings-overrides/plan.md`.
Rationale: Traceability must continue through tasks, implementation notes, verification output, commit text, and PR metadata.
Alternatives considered: Rely on the related `MM-538` artifacts; rejected because final verification must compare against `MM-654`.
Test implications: final verification only.

## Test Tooling

Decision: use repo-standard unit and hermetic integration tiers.
Evidence: repository instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for compose-backed `integration_ci`.
Rationale: Service/API behavior can iterate with focused pytest, but final evidence must use the repo test runner.
Alternatives considered: Provider verification; not applicable because this story needs no external credentials.
Test implications: unit tests are required; integration tests are required if implementation changes affect compose-backed persistence or migration behavior.
