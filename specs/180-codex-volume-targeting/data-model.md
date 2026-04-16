# Data Model: Codex Managed Session Volume Targeting

## LaunchRequest

- Purpose: workspace, session, artifact, Codex home paths, image, environment, profile-derived auth target.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## VolumeMount

- Purpose: source volume, target path, mount class.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## ReservedSessionEnvironment

- Purpose: workspace, state, artifact, Codex home, image, control URL, timeout.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-356` as traceability metadata in generated artifacts.
- Never model raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings as persisted or browser-visible fields.
- Prefer compact refs and explicit status values over provider-shaped dictionaries.
