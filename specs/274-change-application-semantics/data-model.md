# Data Model: Change Application, Reload, Restart, and Recovery Semantics

## Setting Descriptor

Represents backend-owned metadata for an editable setting.

Fields relevant to MM-544:
- `key`: stable setting identifier.
- `scope`: user, workspace, system, or operator applicability.
- `apply_mode`: one of `immediate`, `next_request`, `next_task`, `next_launch`, `worker_reload`, `process_restart`, or `manual_operation`.
- `requires_reload`: whether reload is needed after change.
- `requires_worker_restart`: whether worker restart is needed after change.
- `requires_process_restart`: whether process restart is needed after change.
- `applies_to`: affected subsystems such as task creation, workflow runtime, provider profiles, operations, or integrations.
- `diagnostics`: descriptor-level warnings or errors.

Validation rules:
- Every editable descriptor must declare `apply_mode`.
- Restart and reload booleans must be consistent with `apply_mode`.
- `applies_to` must be non-empty when the setting affects a runtime subsystem.

## Settings Change Event

Represents durable evidence that a setting was committed or reset.

Fields:
- `event_type`: e.g. `settings.override.updated` or `settings.override.reset`.
- `key`: setting key.
- `scope`: affected scope.
- `source`: resulting setting source when available.
- `apply_mode`: descriptor apply mode at commit time.
- `affected_systems`: affected subsystems copied from descriptor metadata.
- `actor_user_id`: actor identity when available.
- `changed_at`: event timestamp.
- `old_value` / `new_value`: redacted according to descriptor audit policy.

Validation rules:
- SecretRef values follow descriptor redaction policy.
- Secret-like values must not leak through event read models.
- Events for changed settings must include apply mode and affected systems.

## Activation State

Represents operator-visible status for whether a committed value is already active.

Fields:
- `current_effective_value`: currently resolved value.
- `pending_value`: value awaiting activation when applicable.
- `active`: whether current runtime state reflects the persisted value.
- `apply_mode`: descriptor apply mode.
- `affected_process_or_worker`: affected process, worker, or subsystem label.
- `completion_guidance`: concise operator action to activate pending value.

State transitions:
- `active` for immediate and already-applied values.
- `pending_next_boundary` for next request/task/launch values.
- `pending_reload` for worker reload values.
- `pending_restart` for process restart values.
- `pending_manual_operation` for manual operation values.

## Restored Reference Diagnostic

Represents a broken reference found after settings data is restored without matching external resources.

Fields:
- `code`: stable code such as `unresolved_secret_ref`, `provider_profile_not_found`, `oauth_volume_not_found`, or `restored_reference_missing`.
- `message`: sanitized operator-facing message.
- `severity`: warning or error.
- `details`: non-secret metadata such as reference type, setting key, and launch-blocker flag.

Validation rules:
- Diagnostics must not resolve or expose plaintext secrets.
- Missing restored references must identify the reference category clearly.
- Diagnostics should be available from catalog/effective/diagnostics surfaces where relevant.

## Settings Backup Record

Represents settings-owned data that can appear in backup/export surfaces.

Allowed content:
- setting keys,
- non-sensitive values,
- SecretRef values,
- resource references,
- audit records,
- metadata.

Forbidden content:
- raw managed secret plaintext,
- OAuth state blobs,
- decrypted files,
- generated credential config,
- large artifacts,
- workflow payloads.
