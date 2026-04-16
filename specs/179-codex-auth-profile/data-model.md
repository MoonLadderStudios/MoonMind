# Data Model: Codex Auth Volume Profile Contract

## ProviderProfile

- Purpose: profile id, runtime id, provider id, credential source, materialization mode, volume ref, mount path, slot policy.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## OAuthVerificationResult

- Purpose: secret-free readiness/failure metadata.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## ProfileSnapshot

- Purpose: serialized profile view exposed to API/workflow consumers.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-318` as traceability metadata in generated artifacts.
- Never model raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings as persisted or browser-visible fields.
- Prefer compact refs and explicit status values over provider-shaped dictionaries.
