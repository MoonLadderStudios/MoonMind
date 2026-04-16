# Data Model: Per-Run Codex Home Seeding

## CodexRuntimeSession

- Purpose: workspace path, artifact spool, per-run Codex home, auth source path, app-server command.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## AuthSeedEntry

- Purpose: source name, type, eligibility, copy result.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## OperatorEvidenceRef

- Purpose: logs, summaries, diagnostics, artifact refs.
- Validation: values must be explicit, non-secret, and safe to serialize across the story boundary.
- Relationships: participates only in this story's declared source-design coverage and dependency chain.
- State transitions: invalid or unsupported values fail fast before externally visible side effects.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-357` as traceability metadata in generated artifacts.
- Never model raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings as persisted or browser-visible fields.
- Prefer compact refs and explicit status values over provider-shaped dictionaries.
