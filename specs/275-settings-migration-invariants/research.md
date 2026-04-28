# Research: Settings Migration Invariants

## Input Classification

Decision: `MM-546` is a single-story runtime feature request.
Evidence: The Jira brief has one maintainer story, one source design, and a bounded acceptance set around migrations, non-goals, invariants, and tests.
Rationale: It does not ask to split the whole Settings System design; it selects sections 4, 24, 25, 28, and 29 for one safety-gate story.
Alternatives considered: Treating `docs/Security/SettingsSystem.md` as a broad declarative design was rejected because the Jira brief already selected one independently testable slice.
Test implications: Unit and API boundary tests are required before implementation.

## FR-002 Rename Migration

Decision: Missing; add explicit migration rules that let a new descriptor resolve an old override key.
Evidence: `SettingsCatalogService._resolve_value_from_overrides()` only checks the current descriptor key. No migration/deprecation rule type exists.
Rationale: Without an explicit rule, a renamed setting silently loses stored operator intent.
Alternatives considered: Automatically checking all historical keys was rejected because it would hide compatibility behavior and violate fail-fast pre-release policy.
Test implications: Unit and API tests must prove effective value preservation and migration diagnostics/audit evidence.

## FR-003 Removed Or Deprecated Settings

Decision: Missing; add removed/deprecated rule handling that rejects new writes and exposes existing rows in diagnostics.
Evidence: Unknown keys return `setting_not_exposed`; existing removed-key rows are invisible to diagnostics because only current registry entries are inspected.
Rationale: The source design requires preserving or explaining existing values and avoiding silent loss.
Alternatives considered: Leaving removed keys as ordinary unknown keys was rejected because it does not explain deprecated existing values.
Test implications: API tests must prove writes are rejected without echoing submitted values and diagnostics include safe deprecated-key evidence.

## FR-004 Type Changes

Decision: Missing; enforce expected override schema version for persisted rows.
Evidence: `SettingsOverride.schema_version` exists but effective-value resolution ignores it.
Rationale: A schema version mismatch is a deterministic signal that an explicit migration is required before resolving the value.
Alternatives considered: Revalidating old JSON against the new type was rejected because it can reinterpret values ambiguously.
Test implications: Unit tests must prove mismatched schema versions produce an error diagnostic/pending state instead of a normal effective value.

## Existing Invariant Coverage

Decision: Existing behavior is strong but needs a single MM-546 invariant test.
Evidence: Tests already cover unexposed key rejection, invalid scopes, type validation, constraints, SecretRef safety, provider profile refs, audit redaction, reset inheritance, and version conflict handling.
Rationale: MM-546 requires these as regression-gate evidence rather than tribal knowledge.
Alternatives considered: Rewriting all existing tests was rejected; focused invariant tests can cite existing coverage and add the missing migration gate.
Test implications: Add a compact catalog invariant test and final targeted test run.
