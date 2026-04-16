# Data Model: Auth Operator Diagnostics

## OAuthSessionProjection

- `session_id`: OAuth enrollment session identifier.
- `runtime_id`: Runtime family identifier.
- `profile_id`: Provider Profile identifier selected for enrollment.
- `status`: Current OAuth enrollment state.
- `created_at`: Session creation timestamp.
- `expires_at`: Optional expiration timestamp.
- `terminal_session_id`: Optional terminal transport identifier.
- `terminal_bridge_id`: Optional terminal bridge identifier.
- `session_transport`: Optional transport kind.
- `failure_reason`: Redacted failure reason.
- `profile_summary`: Optional `ProviderProfileSummary`.

Validation rules:
- Failure reason must be redacted before serialization.
- Projection must not include credential file contents, raw auth-volume listings, environment dumps, or runtime-home directory contents.

## ProviderProfileSummary

- `profile_id`: Provider Profile identifier.
- `runtime_id`: Runtime family identifier.
- `provider_id`: Provider identifier.
- `provider_label`: Optional human-readable provider label.
- `credential_source`: Credential source enum.
- `runtime_materialization_mode`: Materialization mode enum.
- `account_label`: Optional account label.
- `enabled`: Whether the profile can be selected.
- `is_default`: Whether it is the default profile.
- `rate_limit_policy`: Rate limit behavior.

Validation rules:
- Summary excludes `secret_refs`, `env_template`, `file_templates`, `home_path_overrides`, and raw command behavior.
- Summary may include volume refs only through managed-session diagnostics, not as raw file listings.

## AuthMaterializationDiagnostics

- `component`: Owning component for the diagnostic state.
- `readiness`: `ready` or `failed`.
- `profile_ref`: Optional selected Provider Profile identifier.
- `runtime_id`: Optional runtime family identifier.
- `credential_source`: Optional credential source.
- `runtime_materialization_mode`: Optional materialization mode.
- `volume_ref`: Optional provider profile volume ref.
- `auth_mount_target`: Optional managed-session auth mount target.
- `codex_home_path`: Workspace-scoped Codex home path.
- `validation_failure_reason`: Optional sanitized failure reason.

Validation rules:
- Values must be compact strings safe for workflow/activity metadata.
- Secret-like values, raw credential contents, environment dumps, raw volume listings, runtime-home contents, and terminal scrollback are forbidden.

## DurableExecutionEvidence

- `live_logs_ref`: Existing live log surface or stream metadata.
- `artifact_refs`: Existing artifact refs for outputs, diagnostics, summaries, and reset/control boundaries.
- `diagnostics_ref`: Existing diagnostics artifact ref.
- `summary_ref`: Existing summary artifact ref.
- `reset_boundary_ref`: Existing reset/control-boundary artifact ref.

Validation rules:
- Evidence points to durable refs and summaries, not auth volumes, runtime homes, or terminal scrollback.
