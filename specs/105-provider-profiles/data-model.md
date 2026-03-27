# Data Model: Provider Profiles

## `managed_agent_provider_profiles` (Supersedes `managed_agent_auth_profiles`)

**Fields**:
- `profile_id` (str, PK): e.g. `claude_minimax_m27`.
- `runtime_id` (str, Indexed): e.g. `claude_code`.
- `provider_id` (str, Indexed): e.g. `minimax`.
- `provider_label` (str | null)
- `credential_source` (str): `oauth_volume`, `secret_ref`, `none`
- `runtime_materialization_mode` (str): `oauth_home`, `api_key_env`, `env_bundle`, `config_bundle`, `composite`
- `account_label` (str | null)
- `enabled` (bool, Indexed)
- `tags` (JSONB): e.g. `["default"]`
- `priority` (int): default 100
- `volume_ref` (str | null)
- `volume_mount_path` (str | null)
- `secret_refs` (JSONB): dict of internal name to secret literal `db://...`
- `clear_env_keys` (JSONB): list of strings to blank from environment
- `env_template` (JSONB): dict of string to dict mapping literals or secret_refs
- `file_templates` (JSONB): list of file generation instructions
- `home_path_overrides` (JSONB): dict of string to string overrides
- `command_behavior` (JSONB): dict of arbitrary runtime flags
- `max_parallel_runs` (int): Slots available.
- `cooldown_after_429_seconds` (int)
- `rate_limit_policy` (str)
- `max_lease_duration_seconds` (int)
- `owner_user_id` (UUID | null)
- `created_at` (timestamptz)
- `updated_at` (timestamptz)

## `AgentExecutionRequest` enhancements
- `profile_selector`: A nested optional parameter.
  - `provider_id` (str | null)
  - `tags_any` (list[str])
  - `tags_all` (list[str])
  - `runtime_materialization_mode` (str | null)
