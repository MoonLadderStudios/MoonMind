# Data Model: Provider Profiles Phase 2

## Entities Overview

### `ManagedAgentProviderProfile` (Stored as SQLAlchemy model)
Represents a configured agent integration ready for task execution.

**New fields:**
- `default_model` (String, Optional): An explicit foundation model selection overriding the agent's default logic.
- `model_overrides` (JSONB / Dict[str, str]): Extra configurations provided to the agent CLI or environment (e.g., replacing base URLs or model aliases within a composite bundle).

**Existing relevant fields requiring validation:**
- `secret_refs`: Dictionary of environment variables matching `SecretRef` names.
- `env_template`: Static environment values injected at runtime.
- `clear_env_keys`: Variables to scrub from the base execution environment.
- `file_templates`: Configuration strings to be materialized on disk (like `.agentkit.yaml`).
- `command_behavior`: Flags or overrides for CLI behavior.

### `ManagedAgentProviderProfileCreate` & `Update` (Pydantic Models)
The service layer inputs that validate the above shape. Specifically, these models validate that `secret_refs` are well-structured (not raw secret values).
