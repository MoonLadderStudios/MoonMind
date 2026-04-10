# Data Model: Codex CLI OpenRouter Phase 1

## ManagedRuntimeProfile

- `provider_id`: runtime-target provider identity (`openrouter`).
- `provider_label`: operator-facing provider name (`OpenRouter`).
- `credential_source`: `secret_ref` for launch-time API-key resolution.
- `runtime_materialization_mode`: `composite` because launch needs both env injection and generated config.
- `env_template`: environment declarations that may reference secret roles through `from_secret_ref`.
- `file_templates`: list of path-aware generated-file contracts.
- `home_path_overrides`: environment keys such as `CODEX_HOME` that point the runtime at generated support files.
- `command_behavior`: profile-driven strategy hints such as `suppress_default_model_flag`.

## RuntimeFileTemplate

- `path`: rendered absolute target path under the run-scoped support directory.
- `format`: serializer for generated content (`text`, `toml`, `json`).
- `merge_strategy`: Phase 1 supports `replace` only.
- `content_template`: structured content rendered with runtime path variables and secret-role references.
- `permissions`: optional filesystem mode; defaults to `0600`.

## Auto-Seeded OpenRouter Profile

- `profile_id`: `codex_openrouter_qwen36_plus`
- `runtime_id`: `codex_cli`
- `provider_id`: `openrouter`
- `default_model`: `qwen/qwen3.6-plus`
- `secret_refs.provider_api_key`: `env://OPENROUTER_API_KEY`
- `env_template.OPENROUTER_API_KEY`: `{"from_secret_ref": "provider_api_key"}`
- `file_templates[0]`: `{{runtime_support_dir}}/codex-home/config.toml`
- `home_path_overrides.CODEX_HOME`: `{{runtime_support_dir}}/codex-home`
- `command_behavior.suppress_default_model_flag`: `true`
