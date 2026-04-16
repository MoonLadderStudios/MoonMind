# Data Model: OAuth Session State and Verification Boundaries

## OAuthSession

- Purpose: session id, owner, status, transport, volume ref, timestamps, terminal state.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## VerificationResult

- Purpose: status, reason, provider/runtime ids, no secret values.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## MaterializationCheck

- Purpose: profile id, runtime id, verification status before ready state.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-318` as traceability metadata in generated artifacts.
- Never model raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings as persisted or browser-visible fields.
- Prefer compact refs and explicit status values over provider-shaped dictionaries.
