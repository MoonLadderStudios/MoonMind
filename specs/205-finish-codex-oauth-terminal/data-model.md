# Data Model: Finish Codex OAuth Terminal Flow

## Provider Profile

Represents the selected Codex runtime credential profile.

Fields used by this story:
- `profile_id`: stable operator-visible identifier.
- `runtime_id`: must be `codex_cli` for this first supported flow.
- `provider_id` / `provider_label`: provider metadata passed through to final profile registration.
- `credential_source`: `oauth_volume` after successful finalization.
- `runtime_materialization_mode`: `oauth_home` after successful finalization.
- `volume_ref`: durable auth volume reference, defaulting through Codex OAuth provider defaults when not supplied.
- `volume_mount_path`: enrollment/verification mount path, defaulting through Codex OAuth provider defaults when not supplied.
- `account_label`: safe display label supplied by the operator/session.
- policy fields: `max_parallel_runs`, `cooldown_after_429_seconds`, `rate_limit_policy`, `enabled`, `is_default`.

Validation:
- Codex OAuth profiles require `volume_ref` and `volume_mount_path`.
- Finalization preserves policy values not owned by credential verification.
- Secret refs and token material are not populated by OAuth finalization.

## OAuth Session

Represents one credential enrollment attempt.

Fields used by this story:
- `session_id`, `runtime_id`, `profile_id`, `requested_by_user_id`.
- `session_transport`: `moonmind_pty_ws` for interactive Codex enrollment.
- `status`: pending, starting, bridge_ready, awaiting_user, verifying, registering_profile, succeeded, failed, cancelled, expired.
- `terminal_session_id`, `terminal_bridge_id`, `container_name`.
- `volume_ref`, `volume_mount_path`, `account_label`.
- `metadata_json`: provider metadata, policy values, terminal attach token hash/use marker, safe terminal connection counters.
- timestamps: created, started, connected, disconnected, completed, cancelled, expires.
- `failure_reason`: sanitized operator-facing reason.

State transitions:
- pending -> starting -> bridge_ready -> awaiting_user -> verifying -> registering_profile -> succeeded.
- active states may transition to failed, cancelled, or expired.
- failed/cancelled/expired sessions may be retried via a new session preserving safe profile/volume metadata.

## Terminal Attachment

Represents a browser connection to the OAuth session terminal.

Fields:
- one-time attach token hash stored in session metadata.
- websocket URL returned to browser, with token used only for connection setup.
- connection metadata: connected/disconnected timestamps, resize dimensions, input/output/heartbeat counts, close reason.

Validation:
- Token must match hash, be unused, and belong to an active attachable session.
- Reuse, expired sessions, wrong owner, missing runner, and unsupported frames fail closed.
- Generic exec/task-terminal frames are rejected.

## Verification Result

Secret-free result from Codex credential verification.

Fields:
- `verified`: boolean.
- `status`: verified, failed, skipped.
- `runtime_id`.
- `reason`: compact symbolic reason.
- safe counts or booleans for validated expected artifacts.

Validation:
- Must not include token values, raw auth JSON, raw config, auth headers, private keys, or raw auth-volume paths beyond safe configured refs.
- Codex verification should validate usable structure, not only file existence.
