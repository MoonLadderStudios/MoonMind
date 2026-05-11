# Data Model: Sparse Settings Override Persistence and Reset

## Setting Override

Purpose: Stores one sparse override for one setting key at user or workspace scope.

Fields:

- `id`: unique identifier for the override row.
- `scope`: `user` or `workspace`.
- `workspace_id`: workspace subject for the override, or the default subject marker when not applicable.
- `user_id`: user subject for the override, or the default subject marker when not applicable.
- `key`: stable setting key.
- `value_json`: stored override value. May be null to represent an intentional null override.
- `schema_version`: stored value schema version.
- `value_version`: optimistic concurrency version.
- `created_by` / `updated_by`: optional actor identity.
- `created_at` / `updated_at`: audit timestamps.

Relationships and constraints:

- Unique by `scope`, `workspace_id`, `user_id`, and `key`.
- Only user and workspace scopes are persisted by this story.
- Absence of a row means inherit; absence is distinct from a row whose value is null.

Validation rules:

- Values must match the registered setting schema.
- Values must stay within the configured serialized size limit of 16 KiB.
- Values must not contain raw secret plaintext, OAuth session data, decrypted credentials, generated config containing secrets, large artifacts, workflow payloads, or operational command history.
- SecretRef and resource-reference values are stored only as references and are not resolved to plaintext during override persistence.

## Effective Setting Value

Purpose: Represents the resolved setting returned to callers.

Fields:

- `key`: setting key.
- `scope`: requested effective scope.
- `value`: resolved value.
- `source`: one of inherited/configured/default, `workspace_override`, `user_override`, or intentional-null override source.
- `source_explanation`: human-readable source explanation.
- `value_version`: version of the winning override or inherited baseline.
- `diagnostics`: structured validation, migration, or resolution diagnostics.

State transitions:

- No override -> inherited effective value.
- Workspace override saved -> workspace effective source becomes `workspace_override`.
- User override saved -> user effective source becomes `user_override`.
- User override reset -> user effective value inherits workspace override or default.
- Workspace override reset -> workspace effective value inherits configured/default value.

## Settings Audit Event

Purpose: Records non-secret settings changes and reset events.

Fields:

- `event_type`: override update or reset event.
- `key`: setting key.
- `scope`: user or workspace.
- `workspace_id` / `user_id`: scoped subject identity.
- `actor_user_id`: optional actor identity.
- `old_value_json` / `new_value_json`: redacted or stored values according to audit policy.
- `redacted`: whether values were redacted.
- `reason`: optional operator-provided reason.
- `created_at`: event timestamp.

Validation rules:

- Audit events must not expose raw secret values.
- Reset writes an audit event but must not delete existing audit history.
