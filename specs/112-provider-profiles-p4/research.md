# Research: Provider Profiles Phase 4

## Findings

- **Materializer Subsystem**: The codebase requires a `ProviderProfileMaterializer` that securely pipelines environment construction without retaining plaintext credentials in Temporal traces.
- **Secret Resolution**: MoonMind already provides a `ManagedSecret` model. The `db_encrypted` secrets can be decrypted locally at launch using Python's cryptography primitive matching Temporal data converter patterns, allowing the plaintext to only hit the process environment.
- **File Templates**: Anthropic explicitly expects `ANTHROPIC_API_KEY`, but Claude models hosted by MiniMax require modifying the base URL or using specific environment configurations. File templates allow injecting these configs statelessly at runtime.
- **Temporal Output Safety**: To prevent secret leakage, any structured data returned back to Temporal from the launch activity must explicitly exclude or redact anything originating from `secret_refs`.

## Decisions

- **Decision 1**: Introduce `ProviderProfileMaterializer` abstraction.
  - **Rationale**: Isolates the 9-step processing order from `ManagedAgentAdapter`, improving testability.
- **Decision 2**: Define `SecretResolverBoundary`.
  - **Rationale**: Encapsulates DB lookups and decryption away from the core execution logic, ensuring only explicitly authorized runtime launches can fetch the plaintext values.
- **Decision 3**: Delete Legacy Auth Mode Branches.
  - **Rationale**: Replaces `auth_mode` with generic `credential_source` + `runtime_materialization_mode` to finalize Phase 4 semantics.
