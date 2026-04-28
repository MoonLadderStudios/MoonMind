# Data Model: Scoped Override Persistence and Inheritance

## SettingOverride

- `id`: UUID primary key.
- `scope`: one of `user` or `workspace` for persisted editable overrides.
- `workspace_id`: optional UUID subject for workspace-scoped rows.
- `user_id`: optional UUID subject for user-scoped rows.
- `key`: stable setting key from the backend-owned registry.
- `value_json`: JSON value. May be boolean, number, string, null, small list/object where allowed, SecretRef string, or resource reference.
- `schema_version`: integer schema version for future migrations.
- `value_version`: integer optimistic concurrency version; starts at 1 and increments on update.
- `created_at` / `updated_at`: timestamps.
- `created_by` / `updated_by`: optional actor UUIDs.

Validation rules:

- Unique row identity is `(scope, workspace_id, user_id, key)`.
- Scope must be allowed by the descriptor.
- Value must match descriptor type/options/constraints.
- Unsafe raw secrets, OAuth state, decrypted credential files, generated credential config, large artifacts, workflow payloads, and operational history are rejected.
- Absence of a row means inherit; a row with `value_json = null` is an intentional null override.

## SettingsAuditEvent

- `id`: UUID primary key.
- `event_type`: `settings.override.updated` or `settings.override.reset`.
- `key`: stable setting key.
- `scope`: affected scope.
- `workspace_id` / `user_id`: optional subject identity.
- `actor_user_id`: optional actor UUID.
- `old_value_json`: old value when descriptor audit policy allows it.
- `new_value_json`: new value when descriptor audit policy allows it.
- `redacted`: whether values were redacted according to audit policy.
- `reason`: optional caller-provided reason.
- `request_id`: optional request correlation ID.
- `created_at`: timestamp.

Validation rules:

- Reset deletes only `SettingOverride`; audit rows remain.
- Audit values are redacted for descriptors whose audit policy requires redaction.

## EffectiveSettingValue

Extends the existing read model by setting:

- `value`: winning inherited or override value.
- `source`: `user_override`, `workspace_override`, `environment`, `config_or_default`, `default`, or `missing`.
- `override_value`: descriptor-level current override value when present.
- `value_version`: winning override version when an override wins, otherwise `1`.
- `diagnostics`: explicit non-secret resolver diagnostics.

State transitions:

```text
no row -> create override row version 1
override row version N -> update row version N+1 when expected version matches
override row -> delete row on reset and write audit event
stale expected version -> no state change
invalid value -> no state change
```
