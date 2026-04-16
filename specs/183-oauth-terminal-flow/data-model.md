# Data Model: OAuth Terminal Enrollment Flow

## OAuthSession

- Purpose: owner, provider, volume, status, transport metadata.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## AuthRunner

- Purpose: container id, bootstrap command, target volume, lifecycle terminal reason.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## TerminalConnection

- Purpose: attach owner, token, resize, heartbeat, close metadata.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-318` as traceability metadata in generated artifacts.
- Never model raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings as persisted or browser-visible fields.
- Prefer compact refs and explicit status values over provider-shaped dictionaries.
