# Data Model: Settings Migration Invariants

## SettingMigrationRule

Represents an explicit, checked-in migration or deprecation rule for one historical setting key.

Fields:
- `old_key`: historical settings key that may still exist in persisted overrides.
- `new_key`: replacement descriptor key when the setting was renamed.
- `state`: `renamed`, `deprecated`, `removed`, or `type_changed`.
- `expected_schema_version`: schema version that current resolution can safely interpret.
- `message`: redacted operator-facing explanation for diagnostics.

Validation rules:
- `renamed` rules must include `new_key`.
- `removed` and `deprecated` rules must reject new writes to `old_key`.
- `type_changed` rules must require explicit schema-version compatibility before resolving old persisted JSON.
- Diagnostic messages must not include raw override values or secret-like content.

## Setting Override

Existing persisted override row.

Relevant fields:
- `key`
- `scope`
- `value_json`
- `schema_version`
- `value_version`

State transitions:
- Current key override resolves normally when schema version matches.
- Old key override resolves through an explicit rename rule when no current-key override exists.
- Removed/deprecated old key remains queryable as diagnostic evidence but cannot accept new writes.
- Schema-version mismatch blocks normal resolution until an explicit migration is added.

## Settings Diagnostic

Existing read model extended by migration/deprecation evidence.

New diagnostic codes:
- `setting_renamed_override`: old key override is being used through an explicit rename rule.
- `setting_deprecated_override`: old key override exists and requires migration/removal handling.
- `setting_type_migration_required`: persisted override schema version is not compatible with the current descriptor.

Secret safety:
- Diagnostics may include keys, scopes, versions, and rule state.
- Diagnostics must not include `value_json` or raw secret material.
