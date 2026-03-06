# Data Model: Queue Publish PR Title and Description System

## Overview

This feature does not add database entities. It defines deterministic derivation models for publish text computed at runtime from canonical task payload fields.

## Entities

### PublishTextOverrides

- **Description**: Producer-provided text overrides in `task.publish`.
- **Fields**:
  - `commitMessage: string | null`
  - `prTitle: string | null`
  - `prBody: string | null`
- **Invariants**:
  - Non-empty values take precedence over generated defaults.
  - Whitespace-only values are treated as omitted.

### PublishDerivationContext

- **Description**: Runtime context used for default text derivation.
- **Fields**:
  - `jobId: UUID`
  - `runtimeMode: string` (`codex|gemini|claude` when resolved)
  - `startingBranch: string`
  - `workingBranch: string`
  - `steps: list[ResolvedTaskStep]`
  - `taskInstructions: string | null`
- **Invariants**:
  - Context is available before PR creation command executes.
  - Base/head branches in generated metadata must match publish command semantics.

### DerivedPrTitle

- **Description**: Computed title when no `prTitle` override is provided.
- **Derivation Order**:
  1. First non-empty step title.
  2. First sentence/line of task instructions.
  3. Deterministic fallback (`MoonMind task result`).
- **Invariants**:
  - Full UUID must not appear in derived title.
  - Result is concise for list readability.

### DerivedPrBody

- **Description**: Computed body when no `prBody` override is provided.
- **Sections**:
  - Human-readable summary sentence/section.
  - Metadata footer block:
    - `<!-- moonmind:begin -->`
    - `MoonMind Job: <job-uuid>`
    - `Runtime: <runtime>`
    - `Base: <base-branch>`
    - `Head: <head-branch>`
    - `<!-- moonmind:end -->`
- **Invariants**:
  - Includes full UUID for source-of-truth correlation.
  - Contains stable key names and no secret values.

## State/Flow Notes

1. Publish stage resolves commit message and commits.
2. If `publish.mode=pr`, publish stage resolves PR base/head plus title/body.
3. Publish stage invokes `gh pr create` using resolved values.
4. Publish stage emits events and `publish_result.json` as existing behavior.
