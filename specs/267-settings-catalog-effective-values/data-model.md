# Data Model: Settings Catalog and Effective Values

## Setting Descriptor

Represents one backend-exposed settings catalog entry.

Fields:
- `key`: stable dotted identifier.
- `title`, `description`: display metadata.
- `section`, `category`: settings navigation and grouping metadata.
- `type`, `ui`: value type and suggested generic control.
- `scopes`: allowed scopes for the descriptor.
- `default_value`, `effective_value`, `override_value`: value surfaces for read clients.
- `source`, `source_explanation`: winning source metadata.
- `options`, `constraints`: validation and choice metadata.
- `sensitive`, `secret_role`: security metadata; SecretRef values remain references only.
- `read_only`, `read_only_reason`: mutation availability.
- `requires_reload`, `requires_worker_restart`, `requires_process_restart`: apply metadata.
- `applies_to`, `depends_on`: usage and dependency metadata.
- `order`: deterministic display ordering.
- `audit`: redaction and old/new value storage policy.
- `value_version`: optimistic contract placeholder for future override persistence.
- `diagnostics`: resolver diagnostics.

Validation rules:
- Keys are explicit registry values only.
- Option values are stable contract values.
- SecretRef settings must not contain plaintext secret values.

## Effective Setting Value

Represents the resolved value for one setting at one scope.

Fields:
- `key`
- `scope`
- `value`
- `source`
- `source_explanation`
- `value_version`
- `diagnostics`

Resolution order for this story:
1. Deployment environment alias.
2. Loaded application settings/defaults.
3. Catalog default.
4. Missing diagnostic.

## Setting Diagnostic

Represents non-secret resolver state.

Fields:
- `code`
- `message`
- `severity`
- `details`

Initial diagnostic codes:
- `inherited_null`
- `unresolved_secret_ref`
- `invalid_secret_ref`

## Settings Error

Represents failed settings API requests.

Fields:
- `error`
- `message`
- `key`
- `scope`
- `details`

Initial error codes:
- `unknown_setting`
- `setting_not_exposed`
- `invalid_scope`
- `read_only_setting`
- `no_settings_changed`

## Persistence

No new persistence is introduced for `MM-537`. Scoped override rows, audit rows, optimistic value versions, and durable reset behavior are deferred to `MM-538`.
