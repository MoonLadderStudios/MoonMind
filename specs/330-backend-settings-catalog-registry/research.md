# Research: Backend-Owned Settings Catalog Registry and Descriptor Contract

## FR-001 SettingsRegistry Class — Registration, Uniqueness, Key Format, Eligibility

Decision: implemented; `SettingsRegistry` added to `api_service/services/settings_catalog.py` (line 473).
Evidence: Class owns key-format validation via `_SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")`, duplicate-key detection during `_validate()`, and migration-gate checking. `entries` and `entries_by_key` properties expose eligibility-filtered access.
Rationale: Named component replaces the implicit validation that was previously scattered or absent; single `_validate()` call in `__init__` enforces all invariants at construction time.
Alternatives considered: Leaving validation in `SettingsCatalogService._validate_registry()` was rejected because it would not enforce key format or migration semantics for registries constructed outside the service.
Test implications: unit.

## FR-002 Migration Gate Against Stable-Key Ledger

Decision: implemented; `_CATALOG_KEY_LEDGER` constant with 7 keys and migration-gate logic in `SettingsRegistry._validate()` (line 496).
Evidence: `_CATALOG_KEY_LEDGER: frozenset[str]` defined at line 290; `_validate()` raises `ValueError("catalog_integrity_error: ...")` when a ledger key is absent from the current entry set and has no covering `SettingMigrationRule`. `SettingMigrationRule` dataclass added with `old_key`, `state`, `message`, `new_key`, and `expected_schema_version` fields.
Rationale: Fail-fast at construction time ensures no silent descriptor removal reaches the catalog endpoint. Gate can be bypassed per-instance by passing `stable_key_ledger=None`.
Alternatives considered: CI-only lint check was rejected because it would not catch runtime construction from dynamic registries.
Test implications: unit.

## FR-003 from_pydantic_model() Class Method

Decision: implemented; `SettingsRegistry.from_pydantic_model()` at line 528.
Evidence: Iterates `model_class.model_fields`, checks `json_schema_extra.moonmind.expose == True`, extracts `key`, `section`, `category`, `scopes`, `ui`, `requires_reload`, `apply_mode`, `title`, `description`, `options`, `applies_to`, and `order` from the `moonmind` metadata dict. Skips any field without `moonmind.expose`. Only top-level fields are processed.
Rationale: Additive metadata approach (`json_schema_extra`) means existing Pydantic models do not change behavior for consumers that do not opt in. No reflection beyond `model_fields` is needed.
Alternatives considered: Class decorator approach was rejected because it would require modifying the class definition rather than individual field metadata. Separate YAML metadata file was rejected because it would diverge from the field definition and rot silently.
Test implications: unit.

## FR-004 SettingsCatalogBuilder Class

Decision: implemented; `SettingsCatalogBuilder` added at line 585.
Evidence: Accepts a `SettingsRegistry` and a required `descriptor_fn` callback. `build()` filters entries by `section` and `scope`, groups by `category`, sorts by `order`, and returns `SettingsCatalogResponse`. `SettingsCatalogService` is updated to internally construct a `SettingsCatalogBuilder` and delegate `catalog()` / `catalog_async()` to it.
Rationale: Separating builder from service makes section/scope filtering and category grouping independently testable without standing up a full service instance.
Alternatives considered: Inlining builder logic in `SettingsCatalogService.catalog()` was rejected because it cannot be tested without constructing a full service including database session wiring.
Test implications: unit.

## FR-005 Key Format Validation

Decision: implemented; `_SETTING_KEY_RE` pattern enforced in `SettingsRegistry._validate()`.
Evidence: Pattern `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$` is validated for every entry at registry construction. Entries with keys that do not match raise `ValueError("invalid_key_format: ...")` immediately.
Rationale: Dotted key stability is foundational to the settings API contract; early validation prevents silent exposure of keys that would break URL/JSON safety guarantees.
Alternatives considered: Validating only at API ingestion time was rejected because it would allow invalid keys to reside in the registry tuple until a request triggered the catalog endpoint.
Test implications: unit.

## FR-006 Snapshot Test for Catalog Drift

Decision: implemented; `tests/unit/services/test_settings_catalog_snapshot.py` and `tests/unit/services/snapshots/settings_catalog_snapshot.json` committed.
Evidence: Snapshot contains the 7 current keys with their `type`, `scopes`, and `section`. Snapshot test calls `SettingsCatalogService(env={}).catalog()`, derives the same shape, and asserts equality. Any key added, removed, or changed in `type`/`scopes` causes the test to fail with a descriptive diff.
Rationale: Snapshot approach ensures the test evidence is committed alongside the catalog definition; the developer must intentionally update both when the catalog changes.
Alternatives considered: Schema comparison in an integration test was rejected because it requires a running database; the snapshot must work hermetically in the unit tier.
Test implications: unit.

## FR-007 _CATALOG_KEY_LEDGER Starts with 7 Current Keys

Decision: implemented; ledger initialized from the 7 keys previously in `_REGISTRY`.
Evidence: `_CATALOG_KEY_LEDGER` at line 290 contains `workflow.default_task_runtime`, `workflow.default_publish_mode`, `workflow.default_provider_profile_ref`, `skills.policy_mode`, `skills.canary_percent`, `live_sessions.default_enabled`, and `integrations.github.token_ref`. These match the 7 `SettingRegistryEntry` objects in `_REGISTRY`.
Rationale: Starting the ledger from the existing entries ensures the migration gate does not break any currently running deployment; ledger grows only when new entries are added.
Test implications: unit (snapshot test indirectly verifies ledger coverage).

## FR-008 Traceability

Decision: confirmed; `MM-652` and all S-prefixed coverage IDs are present in `specs/330-backend-settings-catalog-registry/spec.md`.
Evidence: `spec.md` header preserves the verbatim Jira preset brief including coverage IDs S5.1, S5.9, S7.1, S7.2, S8.1–S8.4, S22.4, S22.5, S25.1, S25.20, S26.SettingsRegistry, S26.SettingsCatalogBuilder, S29.1. Commit messages and branch name reference `MM-652`.
Rationale: Traceability from Jira to MoonSpec to code to tests is a first-class project requirement per Constitution §XI.
Test implications: none (artifact review).

## Edge Cases Investigated

**Unsafe field token rejection**: `_UNSAFE_FIELD_TOKENS` tuple at line 52 defines tokens (`secret`, `token`, `password`, `api_key`, etc.) that indicate sensitive fields. The spec requires these to be rejected unless `sensitive=True` and `ui="secret_ref_picker"` or `ui="readonly"`. This invariant is enforced by the existing `_validate_registry()` path and inherited by `SettingsRegistry._validate()`. The `integrations.github.token_ref` entry correctly sets `sensitive=False` (it stores a reference, not the secret itself) and `ui="secret_ref_picker"`.

**Nested sub-model skipping**: `from_pydantic_model()` iterates `model_class.model_fields` only, which returns top-level fields. Nested Pydantic models appear as field types, not expanded fields, so sub-model fields are automatically skipped without extra filtering.

**Duplicate key detection**: `_validate()` uses a `seen: set[str]` accumulator and raises `ValueError(f"duplicate_key: {entry.key!r}")` at the first duplicate, before continuing to process remaining entries.
