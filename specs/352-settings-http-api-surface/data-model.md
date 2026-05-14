# Data Model: Settings HTTP API Surface

## Setting Descriptor

Represents one backend-exposed setting in catalog responses.

Fields:
- `key`: stable dotted setting key.
- `title`, `description`, `category`, `section`: display and grouping metadata.
- `type`, `ui`, `options`, `constraints`: value and rendering contract.
- `scopes`: supported scopes.
- `default_value`, `effective_value`, `override_value`, `source`, `source_explanation`: resolved value metadata.
- `sensitive`, `secret_role`, `audit`: secret and audit policy.
- `read_only`, `read_only_reason`: write availability.
- `requires_reload`, `requires_worker_restart`, `requires_process_restart`, `applies_to`, `depends_on`: application metadata.
- `value_version`: optimistic concurrency version.
- `diagnostics`: descriptor-generation or readiness issues.

Validation rules:
- Only explicitly registered settings may appear.
- A descriptor may only expose scopes declared by the registry entry.
- Sensitive values must be redacted according to descriptor policy.

## Effective Setting Value

Represents the resolved value for one key at one scope.

Fields:
- `key`, `scope`, `value`.
- `source`, `source_explanation`.
- `default_value`, `inheritance_state`, `read_only`, `read_only_reason`.
- `requires_reload`, `requires_worker_restart`, `requires_process_restart`, `applies_to`.
- `value_version`.
- `diagnostics`.

State transitions:
- Default/config/environment source -> workspace override when a workspace value is persisted.
- Workspace override -> user override when a user value is persisted for the same key.
- Override -> inherited value when reset deletes the override row.
- Any source -> diagnostic state when validation or dependency checks detect invalid, unresolved, or policy-blocked values.

## Settings Change Request

Represents a user or workspace write request.

Fields:
- `scope`: `user` or `workspace`.
- `changes`: map of setting key to proposed value.
- `expected_versions`: map of setting key to expected current version.
- `reason`: optional human-readable change reason.

Validation rules:
- Scope must be writable.
- Every key must be exposed, writable, and available at the requested scope.
- Expected versions must match current persisted versions when supplied.
- Values must pass type, constraint, reference, dependency, policy, and unsafe-payload validation.
- The whole request must fail before persistence if any blocking issue exists.

## Settings Validation Result

Represents non-committing validation output for proposed changes.

Fields:
- `scope`.
- `accepted`: boolean.
- `issues`: structured validation issues grouped or listed by key.
- `diagnostics`: redacted setting diagnostics where applicable.

Validation rules:
- Produces no override rows and no settings audit mutation.
- Uses the same validation rules as write requests for proposed values.
- Redacts sensitive values and secret-like payloads.

## Settings Preview Result

Represents non-committing preview output for proposed changes.

Fields:
- `scope`.
- `accepted`: boolean.
- `values`: proposed effective values for changed or affected settings.
- `diffs`: old/new effective-value differences with redaction.
- `dependency_warnings`: warnings for dependent settings or runtime effects.
- `reload_requirements`: affected systems and reload/restart flags.
- `issues`: blocking validation issues.

Validation rules:
- Produces no override rows and no settings audit mutation.
- Must compute proposed effective values from current effective values plus submitted changes.
- Must not reveal raw secret plaintext.

## Settings Audit Record

Represents an audit-visible settings event.

Fields:
- `id`, `event_type`, `key`, `scope`.
- `actor_user_id`, `workspace_id`, `user_id`.
- `old_value`, `new_value`, `redacted`, `redaction_reasons`.
- `reason`, `validation_outcome`, `apply_mode`, `affected_systems`.
- `created_at`.

Validation rules:
- Audit reads are filtered by key and/or scope.
- Values are redacted according to descriptor policy and secret-like value detection.
- SecretRef metadata is shown only when the caller has the required metadata permission.

## Settings Error Envelope

Represents public settings API failures.

Fields:
- `error`: stable error code.
- `message`: user-actionable summary.
- `key`: optional setting key.
- `scope`: optional setting scope.
- `details`: contextual details, excluding duplicated top-level fields and secret plaintext.

Validation rules:
- Every public route failure uses this envelope.
- `details` may include validation sub-code, boundary, rule, blockers, and allowed values.
- Sensitive values must be omitted or redacted.
