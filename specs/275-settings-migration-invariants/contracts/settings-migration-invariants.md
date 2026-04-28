# Contract: Settings Migration Invariants

## Service Boundary

`SettingsCatalogService` must accept an explicit set of migration/deprecation rules and apply them during:
- effective value resolution,
- catalog descriptor diagnostics,
- diagnostics endpoint responses,
- write admission checks.

Rules must be deterministic and fail fast. The service must not infer migrations from prompt text, environment names, or unknown keys.

## API Boundary

### `PATCH /api/v1/settings/{scope}`

When a payload writes a removed or deprecated historical key, the response must be:

```json
{
  "error": "read_only_setting",
  "key": "old.setting.key",
  "scope": "workspace"
}
```

The response must not echo submitted raw values.

### `GET /api/v1/settings/effective/{key}?scope=workspace`

When a current key has a configured rename rule and only the old key has a persisted override, the response must resolve the old value under the current key and include a migration diagnostic:

```json
{
  "key": "new.setting.key",
  "scope": "workspace",
  "source": "migrated_workspace_override",
  "diagnostics": [
    {
      "code": "setting_renamed_override",
      "severity": "warning"
    }
  ]
}
```

### `GET /api/v1/settings/diagnostics?scope=workspace`

Diagnostics must include current descriptor diagnostics and safe deprecated-key diagnostics for historical override rows known through explicit rules. Diagnostic details may include old key, new key, state, and schema version; they must not include raw override values.

## Regression Gate

Tests must preserve the future-integration contract that settings are exposed through descriptors, scoped overrides, server-side validation, auditability, and secret-safe diagnostics only.
