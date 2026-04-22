# Data Model: Route Claude Auth Actions

## Provider Profile Row

Represents one Settings table row for a configured runtime/provider profile.

Fields used by this story:

- `profile_id`: stable profile identifier shown in the row and used for action aria labels.
- `runtime_id`: runtime identifier, retained for Codex OAuth compatibility but not sufficient by itself for Claude capability.
- `provider_id`: provider identifier; `anthropic` identifies Claude Anthropic provider intent when paired with Claude metadata.
- `credential_source`: credential source such as `oauth_volume`, `secret_ref`, or `none`.
- `runtime_materialization_mode`: materialization mode such as `oauth_home` or `api_key_env`.
- `secret_refs`: existing secret reference map; presence may indicate a connected secret-backed Claude profile.
- `command_behavior`: optional trusted metadata for action/capability/readiness hints.
- `enabled`: existing row enabled state.

Validation rules:

- Codex OAuth remains available for rows classified as Codex OAuth-capable by the existing credential/materialization shape.
- Claude actions require a Claude Anthropic provider profile identity plus trusted strategy, capability, or readiness metadata.
- Missing or unsupported metadata fails closed by omitting Claude lifecycle actions.

## Auth Action Classification

Derived row state used only by the frontend render path.

Fields:

- `kind`: `codex_oauth`, `claude_manual`, or `none`.
- `status_label`: optional row-visible auth/readiness text.
- `actions`: ordered row actions with display label and aria label.

State transitions:

- Disconnected Claude metadata -> `Connect Claude`.
- Connected Claude metadata -> supported lifecycle actions (`Replace token`, `Validate`, `Disconnect`).
- Failed or degraded Claude metadata -> status label plus supported recovery actions when metadata allows them.
- Unsupported or missing metadata -> no Claude auth action.

## Claude Auth Metadata

Trusted profile metadata shape interpreted from `command_behavior` or existing readiness fields.

Recommended keys:

- `auth_strategy`: expected value `claude_manual_token` for this story.
- `auth_state`: `not_connected`, `connected`, `failed`, or `degraded`.
- `auth_actions`: array containing supported action identifiers: `connect`, `replace_token`, `validate`, `disconnect`.
- `auth_status_label`: optional secret-free operator-readable status.

Validation rules:

- Raw tokens or secret values must never be present in metadata.
- Unknown action identifiers are ignored.
- Lifecycle labels are rendered only for supported action identifiers.
