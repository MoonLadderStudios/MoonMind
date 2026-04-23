# Data Model: Claude Settings Credential Actions

## Provider Profile Row

- `profile_id`: Stable profile identifier. `claude_anthropic` is the target profile for this story.
- `runtime_id`: Runtime identifier. Claude OAuth actions apply to `claude_code`.
- `provider_id`: Provider identifier. Claude Anthropic actions apply to `anthropic`.
- `credential_source`: Credential backing. `oauth_volume` indicates OAuth home materialization; `secret_ref` indicates Managed Secrets-backed API-key materialization.
- `runtime_materialization_mode`: Runtime materialization mode. `oauth_home` supports OAuth home; `api_key_env` supports `ANTHROPIC_API_KEY`.
- `volume_ref` / `volume_mount_path`: OAuth volume metadata used to start and validate OAuth sessions.
- `command_behavior`: Trusted row metadata for supported auth actions, readiness, strategy, and lifecycle state.

## Claude Credential Action Model

- `kind`: One of existing Codex OAuth, Claude credential methods, or none.
- `actions`: Supported row actions derived from trusted metadata or the canonical `claude_anthropic` OAuth profile shape.
- `statusLabel`: Optional trusted status text for the row.
- `readiness`: Optional trusted readiness metadata.

## Claude Credential Actions

- `connect_oauth`: Renders as `Connect with Claude OAuth`; starts the OAuth session lifecycle for the selected provider profile.
- `use_api_key`: Renders as `Use Anthropic API key`; opens the API-key enrollment flow backed by Managed Secrets.
- `validate_oauth`: Renders as `Validate OAuth`; available only when trusted metadata indicates OAuth volume validation is supported.
- `disconnect_oauth`: Renders as `Disconnect OAuth`; available only when trusted lifecycle metadata indicates disconnect is supported.

## State Rules

- OAuth actions must not open the API-key enrollment drawer.
- API-key actions must not create OAuth sessions.
- Unsupported Claude rows hide credential-method actions.
- Existing Codex OAuth rows keep their Codex OAuth model and labels.
