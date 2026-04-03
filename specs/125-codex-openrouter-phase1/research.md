# Research: Codex CLI OpenRouter Phase 1

## Existing State Audit

- `ManagedRuntimeProfile` already had slots for most rich provider-profile fields, but `file_templates` was still legacy-shaped and the adapter did not populate the richer launch contract.
- `ProviderProfileMaterializer` still assumed `file_templates` was an env-var keyed temporary-file map, which could not express `CODEX_HOME/config.toml` generation.
- `provider_profile_list()` flattened `env_template` into `runtime_env_overrides`, losing the richer provider-profile semantics required by the OpenRouter design.
- `api_service.main._auto_seed_provider_profiles()` seeded OAuth Codex and MiniMax Claude profiles, but had no OpenRouter Codex seed path or persistence for `file_templates`, `home_path_overrides`, and `command_behavior`.

## Chosen Approach

- Promote the launch contract to a path-aware `RuntimeFileTemplate` model and keep rendering at the launcher/materializer boundary.
- Preserve the existing managed-runtime control flow: DB row -> `provider_profile_list()` -> `ManagedAgentAdapter.start()` -> launcher -> strategy.
- Use the design document's exact reference profile (`codex_openrouter_qwen36_plus`) as the seeded profile and cover it with unit tests at each boundary.

## Rejected Alternatives

- Add a separate `codex_openrouter` runtime: rejected because the design doc explicitly treats OpenRouter as a provider for `codex_cli`.
- Keep env-only shaping without generated config: rejected because Codex provider selection belongs in config, not only in `-m`.
- Delay OpenRouter until generic provider-profile Phase 4 is fully generalized: rejected because the feature can be implemented cleanly now and the design doc defines the exact Phase 1 scope.
