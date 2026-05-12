# Research: Effective Value Resolver With Source Explanation and Operator Locks

## FR-001 / Effective Value Contract

Decision: `partial`; effective reads return a value and source, but the output does not yet contain all canonical metadata required by `MM-655`.
Evidence: `api_service/services/settings_catalog.py` `EffectiveSettingValue`; `effective_value_async`; `tests/unit/api_service/api/routers/test_settings_api.py` effective endpoint assertions.
Rationale: The existing shape includes source, source explanation, activation state, pending value, affected process/worker, completion guidance, version, and diagnostics. It lacks default value and read-only metadata that the spec requires for every effective explanation.
Alternatives considered: Use only catalog descriptors for explainability; rejected because the Jira brief and current API include effective read endpoints that should be complete enough for downstream consumers.
Test implications: unit and API tests first for effective output shape and metadata.

## FR-002, FR-004 / Default, Workspace, and User Precedence

Decision: `implemented_verified`; built-in/configured values, workspace overrides, and user overrides already follow the required precedence except for source label normalization.
Evidence: `_resolve_value`, `_resolve_value_from_overrides`; `test_effective_value_reports_environment_source_and_explanation`; `test_workspace_override_persists_and_reports_version`; `test_user_override_wins_and_null_override_is_intentional`; API workspace/user override tests.
Rationale: Current resolver checks user override before workspace override for user scope, workspace override before defaults for workspace scope, and environment/config/default sources when no override exists.
Alternatives considered: Rewrite the precedence engine; rejected because existing behavior is mostly correct and covered.
Test implications: final verify for precedence behavior plus focused source-label tests.

## FR-003, FR-006 / Canonical Source Vocabulary

Decision: `partial`; current source labels need alignment with the documented vocabulary.
Evidence: `_resolve_value` emits `environment`, `config_or_default`, `default`, and `missing`; override resolution emits `workspace_override`, `user_override`, migrated/deprecated labels; source spec requires `default`, `config_file`, `environment`, `workspace_override`, `user_override`, `provider_profile`, `secret_ref`, and `operator_lock`.
Rationale: `config_or_default` conflates two source categories, provider-profile and SecretRef references are currently expressed through the origin that supplied the reference rather than the reference kind, and operator locks are absent.
Alternatives considered: Preserve existing labels and add aliases; rejected by the pre-release compatibility policy and by the spec's stable vocabulary requirement.
Test implications: unit/API tests must define the exact canonical labels and update any affected assertions in one cohesive change.

## FR-005, FR-010 / Operator Locks

Decision: `missing`; operator-lock precedence and read-only reason behavior are not implemented as a winning source.
Evidence: `SettingRegistryEntry` and `SettingDescriptor` include `read_only` and `read_only_reason`; `ensure_write_allowed` rejects read-only settings; no registry entry, resolver branch, source label, or focused test for `operator_lock` was found.
Rationale: Read-only metadata is a useful building block, but it does not satisfy the required chain `built-in default < workspace override < user override < operator lock` or prove locks win over ordinary overrides.
Alternatives considered: Treat existing read-only settings as operator locks; rejected because the source vocabulary and precedence requirement require explicit lock semantics.
Test implications: add failing service and API tests for lock precedence, source `operator_lock`, read-only descriptor output, and populated read-only reason.

## FR-007, FR-013 / Distinct Diagnostic States

Decision: `partial`; several diagnostics exist, but the full `MM-655` matrix is incomplete.
Evidence: `_diagnostics`, `_secret_ref_diagnostic`, `_provider_profile_ref_diagnostic`, migration diagnostics; tests cover `inherited_null`, `unresolved_secret_ref`, provider profile not found/disabled, `setting_type_migration_required`, deprecated/renamed override diagnostics, and invalid SecretRef redaction.
Rationale: The spec requires distinct states for no default, inherited null, intentionally null override, unresolvable SecretRef, missing provider profile, policy-blocked, and post-migration invalid. Current tests do not prove each one as a distinct diagnostic state, and intentionally null overrides intentionally suppress diagnostics today.
Alternatives considered: Rely on generic `inherited_null` and migration diagnostics; rejected because the Jira brief explicitly requires distinct actionable explanations.
Test implications: add diagnostic matrix tests and implement missing state codes/messages.

## FR-008, FR-009 / Explainability Metadata

Decision: `partial`; source, explanation, activation, reload, and affected systems exist, but effective-value output needs default/read-only/locked metadata.
Evidence: `SettingDescriptor` includes `default_value`, `read_only`, `read_only_reason`, reload flags, and `applies_to`; `EffectiveSettingValue` includes activation metadata and affected process/worker but not default/read-only fields; diagnostics endpoint includes read-only fields.
Rationale: Users and operators should be able to answer the section 5.5 questions from the effective resolver contract without stitching together multiple partial responses.
Alternatives considered: Require consumers to call both catalog and diagnostics endpoints; rejected for this story because the resolver itself should produce a complete explanation.
Test implications: unit and API tests for effective value metadata, plus diagnostics route regression tests.

## FR-011 / SecretRef and Provider Profile Safety

Decision: `implemented_unverified`; current code keeps plaintext and provider internals out of settings output, but source-label changes require focused regression coverage.
Evidence: `test_secret_ref_diagnostic_does_not_expose_secret_plaintext`; `test_invalid_secret_ref_environment_value_is_redacted`; provider profile readiness/diagnostic tests in service and API suites.
Rationale: Existing behavior is close to the spec, but new canonical source labels for `secret_ref` and `provider_profile` must not accidentally expose plaintext or inline provider-profile internals.
Alternatives considered: Move provider-profile behavior into settings resolver; rejected because `docs/Security/SettingsSystem.md` keeps profiles as separate resources.
Test implications: add or extend verification tests around reference source labels and redaction.

## FR-012 / Verification Evidence

Decision: `missing`; no final `MM-655` verification evidence exists.
Evidence: `specs/340-effective-value-resolver/spec.md` is newly created.
Rationale: Verification evidence is required after implementation and must compare behavior to the preserved Jira preset brief.
Alternatives considered: Reuse prior Settings verification reports; rejected because issue traceability and operator-lock requirements differ.
Test implications: focused tests, full unit suite, and integration suite when API/DB behavior changes.

## FR-014 / Traceability

Decision: `implemented_unverified`; current spec and plan preserve `MM-655`, but later artifacts must continue the chain.
Evidence: `specs/340-effective-value-resolver/spec.md`; `specs/340-effective-value-resolver/plan.md`.
Rationale: The Jira issue key must remain available in tasks, verification, commit text, and PR metadata.
Alternatives considered: Store traceability only in Jira artifacts; rejected because MoonSpec verification compares local artifacts.
Test implications: final verification only.

## Test Tooling

Decision: use repo-standard unit and hermetic integration tiers.
Evidence: repository instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for required compose-backed `integration_ci`.
Rationale: Resolver and route behavior can be covered with focused unit/API tests; integration is required if changes alter persisted settings, route contracts, startup/migration behavior, or compose-backed API assumptions.
Alternatives considered: Provider verification; not applicable because the story needs no external credentials.
Test implications: unit strategy is mandatory; integration strategy remains explicit and must be run if implementation touches persistence/API surfaces beyond pure Pydantic/service logic.
