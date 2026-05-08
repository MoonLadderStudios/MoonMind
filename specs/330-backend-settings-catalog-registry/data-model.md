# Data Model: Backend-Owned Settings Catalog Registry and Descriptor Contract

## SettingRegistryEntry

The immutable record for one exposed setting, registered in `SettingsRegistry`. All fields are set at construction; no runtime mutation is permitted.

- `key`: stable dotted setting key (e.g., `workflow.default_task_runtime`). Must match `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`. Unique within a registry.
- `title`: operator-facing display title.
- `description`: optional human-readable description.
- `category`: grouping label for the Settings UI (e.g., `"Workflow"`, `"Skills"`, `"Integrations"`).
- `section`: one of `"providers-secrets"`, `"user-workspace"`, or `"operations"`. Determines the settings page section.
- `value_type`: string type label (`"enum"`, `"boolean"`, `"integer"`, `"string"`, `"secret_ref"`).
- `ui`: UI widget hint (`"select"`, `"toggle"`, `"number"`, `"input"`, `"secret_ref_picker"`, `"provider_profile_picker"`, `"readonly"`).
- `scopes`: tuple of applicable `SettingScope` values (`"user"`, `"workspace"`, `"system"`, `"operator"`).
- `order`: integer sort key within a category group; lower values appear first.
- `default_value`: optional default value used when no override or env-var value is present.
- `settings_path`: optional tuple of attribute names for resolving the value from `AppSettings`.
- `env_aliases`: tuple of environment variable names that supply this setting.
- `options`: tuple of `(value, label)` pairs for enum-type settings.
- `constraints`: optional `SettingConstraints` with `minimum`, `maximum`, `min_length`, `max_length`, and `pattern`.
- `sensitive`: `True` only if the value itself is secret (not a reference to a secret).
- `secret_role`: optional role label when `value_type` is `"secret_ref"` (e.g., `"github_token"`).
- `read_only`: `True` when the setting cannot be changed through the API.
- `read_only_reason`: optional message explaining why the setting is read-only.
- `apply_mode`: one of `"immediate"`, `"next_request"`, `"next_task"`, `"next_launch"`, `"worker_reload"`, `"process_restart"`, `"manual_operation"`.
- `requires_reload`: `True` when the setting takes effect only after a worker reload.
- `requires_worker_restart`: `True` when a full worker process restart is needed.
- `requires_process_restart`: `True` when a full process restart is needed.
- `applies_to`: tuple of system-area labels (e.g., `("task_creation", "workflow_runtime")`).
- `depends_on`: tuple of `SettingDependency` objects listing prerequisite settings.
- `audit`: `SettingAuditPolicy` with `store_old_value`, `store_new_value`, and `redact` fields.

Validation rules:
- `key` must match the stable dotted pattern; registry rejects any entry that does not.
- Keys must be unique within a single `SettingsRegistry` instance.
- Fields matching `_UNSAFE_FIELD_TOKENS` (secret, token, password, api_key, etc.) must have `sensitive=True` and `ui` set to `"secret_ref_picker"` or `"readonly"`.
- `from_pydantic_model()` skips any field without `json_schema_extra.moonmind.expose == True`.

## SettingMigrationRule

An immutable record covering a key that has been renamed, deprecated, type-changed, or removed from the registry, allowing the migration gate to pass without raising `catalog_integrity_error`.

- `old_key`: the key as it appeared in the stable-key ledger before the change. Required.
- `state`: one of `"renamed"`, `"deprecated"`, `"removed"`, `"type_changed"`.
- `message`: human-readable explanation of the change. Required.
- `new_key`: replacement key when `state` is `"renamed"`. Required for renames; absent otherwise.
- `expected_schema_version`: ledger schema version this rule targets; must be ≥ 1.

Validation rules:
- `old_key` must be non-empty.
- `state == "renamed"` requires a non-empty `new_key`.
- `expected_schema_version` must be ≥ 1.

## SettingsRegistry

The named component that owns descriptor registration and eligibility filtering. Lives in `api_service/services/settings_catalog.py`.

- `entries`: immutable tuple of `SettingRegistryEntry` objects.
- `entries_by_key`: dict index for O(1) key lookup.
- `migration_rules`: tuple of `SettingMigrationRule` objects covering removed or renamed keys.
- `_stable_key_ledger`: optional `frozenset[str]` of keys that must be present or covered by migration rules. Defaults to `_CATALOG_KEY_LEDGER`.

Construction invariants:
- Key format validated for every entry.
- Duplicate keys rejected immediately.
- Migration gate checked: `_stable_key_ledger - current_keys - migrated_keys` must be empty.

## SettingsCatalogBuilder

The named component that constructs `SettingsCatalogResponse` from a `SettingsRegistry`. Lives in `api_service/services/settings_catalog.py`.

- `_registry`: the `SettingsRegistry` to build from.
- `build(section, scope, descriptor_fn)`: filters entries, groups by category, sorts by `order`, calls `descriptor_fn` per entry, and returns `SettingsCatalogResponse`.

Filtering rules:
- If `section` is provided, entries with a different `section` are excluded.
- If `scope` is provided, entries whose `scopes` tuple does not include that scope are excluded.
- Entries within a category are sorted by `order` ascending.

## _CATALOG_KEY_LEDGER

A `frozenset[str]` constant in `api_service/services/settings_catalog.py` containing the 7 setting keys that are part of the committed stable catalog. Any key present in the ledger but absent from a `SettingsRegistry`'s entries and not covered by a `SettingMigrationRule` triggers `catalog_integrity_error` at registry construction.

Current ledger contents:
- `workflow.default_task_runtime`
- `workflow.default_publish_mode`
- `workflow.default_provider_profile_ref`
- `skills.policy_mode`
- `skills.canary_percent`
- `live_sessions.default_enabled`
- `integrations.github.token_ref`

## Snapshot File

`tests/unit/services/snapshots/settings_catalog_snapshot.json` — committed JSON file containing the expected catalog shape for the 7 default entries. Schema per entry:

```json
{
  "<key>": {
    "scopes": ["<scope1>", "<scope2>"],
    "section": "<section>",
    "type": "<value_type>"
  }
}
```

The snapshot test in `tests/unit/services/test_settings_catalog_snapshot.py` derives this shape from a live `SettingsCatalogService` call and asserts equality. Any drift (added/removed key, changed `type`, `scopes`, or `section`) causes the test to fail with a set-difference message.
