# Quickstart: Backend-Owned Settings Catalog Registry and Descriptor Contract

Use Jira issue `MM-652` and the canonical Jira preset brief preserved in `spec.md` as the source of truth.

## Test-First Plan

1. Add unit tests for `SettingsRegistry` in `tests/unit/services/test_settings_catalog.py`:
   - Migration gate raises `catalog_integrity_error` when a ledger key is absent and no migration rule covers it.
   - Migration gate passes when a `SettingMigrationRule` covers the absent key.
   - Key format validation rejects keys that do not match `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`.
   - Duplicate key raises `ValueError` at construction.
2. Add unit test for `SettingsRegistry.from_pydantic_model()`:
   - Field with `json_schema_extra={"moonmind": {"expose": True, ...}}` produces a `SettingRegistryEntry` with correct metadata.
   - Field without `moonmind.expose` produces no entry.
3. Add unit tests for `SettingsCatalogBuilder.build()`:
   - Section filter excludes entries from other sections.
   - Scope filter excludes entries not in the requested scope.
   - Category grouping produces correct `categories` dict.
   - Entries within a category are ordered by `order` ascending.
4. Commit `tests/unit/services/snapshots/settings_catalog_snapshot.json` with the 7-key catalog shape.
5. Add snapshot drift test in `tests/unit/services/test_settings_catalog_snapshot.py`.
6. Confirm tests fail before implementation, then implement the registry, builder, and `moonmind.expose` metadata changes.

## Unit Test Commands

Focused settings catalog tests only:

```bash
./tools/test_unit.sh tests/unit/services/test_settings_catalog.py
```

Focused snapshot test only:

```bash
./tools/test_unit.sh tests/unit/services/test_settings_catalog_snapshot.py
```

Both settings catalog test modules together:

```bash
./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/services/test_settings_catalog_snapshot.py
```

Final full unit verification (required before declaring complete):

```bash
./tools/test_unit.sh
```

## Integration Test Commands

No new integration tests are required for MM-652; the settings catalog service does not add new database tables and the API routes are unchanged. The existing hermetic integration suite validates the API surface:

```bash
./tools/test_integration.sh
```

To run only the settings-related integration tests when Docker is available:

```bash
pytest tests/integration -m 'integration_ci' -k 'settings' -q --tb=short
```

## End-To-End Validation

1. Confirm `SettingsRegistry` construction with all 7 default entries succeeds (no `ValueError`).
2. Remove one entry from the registry without a `SettingMigrationRule` and confirm construction raises `ValueError` containing `catalog_integrity_error` and the removed key name.
3. Add a `SettingMigrationRule` covering the removed key and confirm construction succeeds.
4. Call `SettingsRegistry.from_pydantic_model()` with a Pydantic model that has one field with `json_schema_extra={"moonmind": {"expose": True, "key": "test.my_setting", "section": "user-workspace", "category": "Test", "scopes": ["workspace"], "ui": "toggle"}}`. Confirm the registry contains one entry with `key="test.my_setting"`.
5. Call `SettingsCatalogBuilder(registry).build(section="user-workspace", descriptor_fn=...)` and confirm only entries in `user-workspace` appear and they are grouped by category.
6. Run the snapshot test with the committed snapshot file and confirm it passes.
7. Mutate one entry's `type` field in memory and re-run the snapshot comparison; confirm it fails with a diff showing the changed key.
8. Run `./tools/test_unit.sh` and confirm all previously passing tests continue to pass alongside the new MM-652 tests.

## Key Files

| File | Purpose |
|---|---|
| `api_service/services/settings_catalog.py` | `SettingsRegistry`, `SettingsCatalogBuilder`, `_CATALOG_KEY_LEDGER`, `_SETTING_KEY_RE` |
| `moonmind/config/settings.py` | `WorkflowSettings` fields with `moonmind.expose` metadata |
| `tests/unit/services/test_settings_catalog.py` | Registry and builder unit tests |
| `tests/unit/services/test_settings_catalog_snapshot.py` | Snapshot drift test |
| `tests/unit/services/snapshots/settings_catalog_snapshot.json` | Committed 7-key catalog snapshot |
