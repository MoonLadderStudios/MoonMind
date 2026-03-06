# Research: Queue Publish PR Title and Description System

## Decision 1: Keep override precedence strict and verbatim

- **Decision**: Use `publish.commitMessage`, `publish.prTitle`, and `publish.prBody` exactly as provided when non-empty.
- **Rationale**: Source requirements explicitly define producer-controlled overrides and prioritize explicit values over generated defaults.
- **Alternatives considered**:
  - Normalize or rewrite override strings: rejected because it violates verbatim override intent.
  - Always generate defaults and ignore overrides: rejected because it breaks producer control and existing contract.

## Decision 2: Derive default PR title using deterministic fallback chain

- **Decision**: Resolve default title in order: first non-empty step title -> first sentence/line of `task.instructions` -> deterministic fallback string.
- **Rationale**: Matches doc contract and produces human-readable titles even when optional fields are omitted.
- **Alternatives considered**:
  - Use only `task.instructions`: rejected because steps can encode stronger intent.
  - Include full UUID in title: rejected because contract says avoid full UUID in title text.

## Decision 3: Generate default PR body with machine-parseable metadata footer

- **Decision**: Generate a summary section plus metadata footer bounded by `<!-- moonmind:begin -->` and `<!-- moonmind:end -->`, including `MoonMind Job`, `Runtime`, `Base`, and `Head`.
- **Rationale**: Enables deterministic job/PR correlation and aligns with source document template.
- **Alternatives considered**:
  - Metadata in title only: rejected because title should stay concise and avoid full UUID.
  - Omit metadata keys: rejected because it weakens machine parsing and reconciliation.

## Decision 4: Scope implementation to canonical task publish path

- **Decision**: Implement in `CodexWorker._run_publish_stage` for canonical `type="task"` flows; do not broaden scope to unrelated legacy paths unless needed for shared logic reuse.
- **Rationale**: User request and source contract target canonical queue task publish behavior.
- **Alternatives considered**:
  - Refactor all publish flows across legacy handlers: rejected for unnecessary risk/scope expansion.

## Decision 5: Validate behavior with unit-level publish text tests

- **Decision**: Add unit tests in `tests/unit/agents/codex_worker/test_worker.py` for title fallback order, body metadata generation, and UUID handling expectations.
- **Rationale**: Fast deterministic coverage for string derivation logic without requiring networked publish execution.
- **Alternatives considered**:
  - Full integration tests invoking real `gh pr create`: rejected due environment dependencies and lower determinism.
