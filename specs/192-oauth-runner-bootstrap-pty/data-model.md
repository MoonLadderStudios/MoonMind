# Data Model: OAuth Runner Bootstrap PTY

## OAuth Enrollment Session

- Purpose: Operator-started Codex OAuth enrollment flow that owns the auth runner lifecycle, target auth volume, terminal state, and terminal outcome.
- Key fields: `session_id`, `runtime_id`, `profile_id`, `volume_ref`, `volume_mount_path`, `session_transport`, `terminal_session_id`, `terminal_bridge_id`, `container_name`, `status`, `failure_reason`, `expires_at`.
- Validation rules:
  - `session_id` and `runtime_id` are required before workflow side effects.
  - Codex OAuth sessions require nonblank `volume_ref` and `volume_mount_path`.
  - Failure reasons must be bounded and redacted before reaching workflow outputs, browser responses, logs, or artifacts.
- State transitions:
  - Startup path: `pending` -> `starting` -> `bridge_ready` -> `awaiting_user`.
  - Terminal paths: `awaiting_user` -> `verifying` -> `registering_profile` -> `succeeded`; `awaiting_user` -> `cancelled`; `awaiting_user` -> `expired`; any startup or verification failure -> `failed`.

## OAuth Provider Spec

- Purpose: Runtime-owned provider defaults used to provision OAuth enrollment without embedding provider command details in workflow input.
- Key fields: `runtime_id`, `auth_mode`, `session_transport`, `default_volume_name`, `default_mount_path`, `provider_id`, `provider_label`, `bootstrap_command`, `success_check`, `account_label_prefix`.
- Validation rules:
  - `bootstrap_command` must be a non-empty ordered list of nonblank strings for supported OAuth runtimes.
  - Unsupported runtime IDs fail fast before runner startup.
  - Command values are configuration metadata; credential values must never appear in provider spec fields.
- Relationships:
  - `OAuth Enrollment Session.runtime_id` resolves one provider spec.
  - The activity/runtime boundary uses the provider spec to build the `Auth Runner Launch` request.

## Auth Runner Launch

- Purpose: Short-lived runner startup request for one OAuth enrollment session.
- Key fields: `session_id`, `runtime_id`, `volume_ref`, `volume_mount_path`, `session_ttl`, `runner_image`, `bootstrap_command`.
- Validation rules:
  - `session_id`, `runtime_id`, `volume_ref`, `volume_mount_path`, and `bootstrap_command` are required.
  - `session_ttl` is clamped to the existing safe session range.
  - Runner image and command values must not expose generic Docker exec or ordinary task terminal behavior.
- State transitions:
  - `requested` -> `started` when the runner process/container is created and terminal metadata is available.
  - `requested` -> `failed` for missing Docker, invalid mount, missing provider command, startup failure, command failure, or startup timeout.

## Auth Runner Result

- Purpose: Secret-free startup result persisted to the OAuth session row and returned to the workflow.
- Key fields: `container_name`, `terminal_session_id`, `terminal_bridge_id`, `session_transport`, `expires_at`, `failure_reason`.
- Validation rules:
  - Successful results include nonblank terminal and container identifiers.
  - Failed results expose only bounded redacted failure categories.
  - Raw command output, credential files, token values, environment dumps, and raw auth-volume listings are forbidden.

## Runner Cleanup Result

- Purpose: Idempotent cleanup summary for success, failure, expiry, cancellation, API-finalize, and repeated cleanup paths.
- Key fields: `session_id`, `container_name`, `stopped`, `reason`.
- Validation rules:
  - Missing or already-stopped containers return secret-free no-op outcomes.
  - Cleanup failures are logged as bounded metadata and do not expose raw credential material.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-361` as traceability metadata in generated artifacts, verification output, commit text, and pull request metadata.
- Keep OAuth runner terminal evidence separate from managed Codex task execution evidence.
- Prefer compact refs and explicit status values over provider-shaped dictionaries.
