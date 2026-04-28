# Data Model: Generated User and Workspace Settings UI

## Setting Descriptor

- `key`: Stable setting identifier used for patch and reset calls.
- `title`: User-visible row title.
- `description`: Optional user-visible description.
- `category`: Category group in the User / Workspace section.
- `section`: Must be `user-workspace` for this UI.
- `type` and `ui`: Backend-owned value and control shape.
- `scopes`: Scopes where the setting is available.
- `effective_value`: Current resolved value for display and editing.
- `source` and `source_explanation`: Current source badge and explanation.
- `options`: Select options when applicable.
- `constraints`: Numeric or string constraints.
- `sensitive` and `secret_role`: SecretRef metadata; plaintext secrets are not represented.
- `read_only` and `read_only_reason`: Lock state for ordinary editing.
- `requires_reload`, `requires_worker_restart`, `requires_process_restart`: Runtime effect badges.
- `applies_to`: Affected subsystems used in preview and row metadata.
- `value_version`: Expected version used for save.
- `diagnostics`: Backend diagnostics displayed without secret expansion.

## Pending Setting Change

- `key`: Descriptor key.
- `scope`: Active user or workspace scope.
- `oldValue`: Descriptor `effective_value`.
- `newValue`: Local user-entered value.
- `expectedVersion`: Descriptor `value_version`.
- `isValid`: Client-side shape sanity for obvious UI constraints; backend remains authoritative.
- `affectedSubsystems`: Descriptor `applies_to`.
- `reloadRequirements`: Derived from descriptor reload/restart flags.

## Settings Scope

- `workspace`: Shared workspace defaults and overrides.
- `user`: Personal settings inheriting workspace values where the backend exposes them.

State transitions:

1. Catalog loaded -> descriptors render by category.
2. Descriptor edited -> pending change created.
3. Discard -> pending changes cleared.
4. Save -> `PATCH /settings/{scope}` -> catalog invalidated/refetched.
5. Reset -> `DELETE /settings/{scope}/{key}` -> catalog invalidated/refetched.
