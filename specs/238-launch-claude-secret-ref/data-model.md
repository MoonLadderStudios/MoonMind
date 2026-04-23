# Data Model: Launch Claude Secret Ref

## Claude Anthropic Provider Profile

- `profile_id`: Stable profile identifier. For this story, `claude_anthropic`.
- `runtime_id`: Runtime family. Must target Claude Code.
- `provider_id`: Provider identity. Must target Anthropic.
- `credential_source`: Must indicate secret-reference credentials for the launch path.
- `runtime_materialization_mode`: Must indicate API-key environment materialization.
- `secret_refs`: Compact references to managed secrets. The required binding is `anthropic_api_key`.
- `clear_env_keys`: Environment keys removed from the inherited launch environment before injecting profile-derived values.
- `env_template`: Launch environment mapping. `ANTHROPIC_API_KEY` must render from `anthropic_api_key`.

Validation rules:
- Raw secret values must not be stored in this profile.
- Missing or unreadable `anthropic_api_key` prevents launch.
- `clear_env_keys` is applied before `env_template` injection.

## Managed Secret Binding

- `alias`: The profile-local name used by templates, `anthropic_api_key`.
- `reference`: The durable secret reference, for example a `db://` managed-secret slug.
- `resolved_value`: Plaintext credential available only inside launch materialization memory.

Validation rules:
- Durable workflow/activity payloads carry the reference, not `resolved_value`.
- Resolution errors must be reported without including `resolved_value`.

## Launch Materialization Result

- `environment`: Process environment prepared for Claude Code. It may contain `ANTHROPIC_API_KEY` only at the launch boundary.
- `command`: Command template used to start Claude Code.
- `diagnostics`: Secret-free status or error information.

State transitions:
- `profile_selected` -> `conflicts_cleared` -> `secret_resolved` -> `env_rendered` -> `process_start_requested`.
- On missing or unreadable binding: `profile_selected` -> `conflicts_cleared` -> `secret_resolution_failed`; no process start occurs.
