# Data Model: Provider Profiles Phase 4

## Entities

### `ManagedRuntimeProfile` (Updates)
- **`credential_source`**: Enum (`API_KEY`, `OAUTH_VOLUME`, `ENV_BUNDLE`, etc.) indicating how secrets are injected.
- **`runtime_materialization_mode`**: String (`oauth_home`, `anthropic`, `minimax`, `direct_api`) driving the strategy.
- **`secret_refs`**: Dict of generic keys to DB `ManagedSecret` IDs, resolved at container start.
- **`clear_env_keys`**: List of strings containing env vars to prune from the template before launching.
- **`file_templates`**: Dict mapping container paths to strings (can include Handlebars-style resolved secrets).
- **`command_behavior`**: String for strategy execution overrides.

### `ProviderProfileMaterializer` (Internal Runtime Concept)
- Assembles standard dictionary of environments.
- Returns a tuple of `(Dict[str, str] env, List[str] command)`.
