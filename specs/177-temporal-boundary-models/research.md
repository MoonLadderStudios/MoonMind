# Research: Temporal Boundary Models

## Boundary Inventory Shape

Decision: Represent each covered boundary as a Pydantic `TemporalBoundaryContract` with a closed `kind`, stable Temporal name, request model reference, optional response model reference, schema home, coverage IDs, and compatibility status.

Rationale: MM-327 needs a reviewable serialized-contract inventory before broad migration work. Pydantic keeps validation behavior consistent with the source design and allows tests to reject anonymous or blank contract metadata.

Alternatives considered: A Markdown-only inventory was rejected because it cannot enforce aliases, strict extra-field rejection, or catalog consistency. A dynamic AST scanner was rejected for this story because it would broaden scope into static analysis gates covered by a later story.

## Coverage Scope

Decision: Cover representative public Temporal boundary families that establish the canonical contract surface: activity catalog entries, external-agent activity contracts, managed-session workflow input/message/query/continuation contracts, and run workflow message payloads already represented in existing schema modules.

Rationale: The story is STORY-001 and explicitly out of scope for full call-site migration. A deterministic seed inventory creates the model ownership surface and documents remaining migration work without renaming existing Temporal names.

Alternatives considered: Modeling every catalog entry in one change was rejected as too broad for one independently testable story. Converting workflow call sites immediately was rejected because the Jira brief excludes broad implementation task lists and data converter rollout.

## Documentation Placement

Decision: Keep implementation backlog and compatibility-sensitive notes in `docs/tmp/177-TemporalBoundaryModels.md`.

Rationale: Constitution principle XII and DESIGN-REQ-021 require canonical docs to remain desired-state descriptions. The tracker can be removed when the migration work completes.

Alternatives considered: Updating `docs/Temporal/TemporalTypeSafety.md` directly was rejected because it would turn canonical documentation into a construction diary.

## Test Strategy

Decision: Add unit tests for model validation and deterministic inventory content, plus an integration-labeled test that compares covered activity names against the default activity catalog and covered workflow/message names against known constants.

Rationale: Unit tests provide fast schema evidence. Integration-style boundary tests exercise the real catalog and catch name drift without requiring provider credentials.

Alternatives considered: Full Temporal test-server round trips were rejected for this story because the active scope is inventory/model ownership rather than call-site conversion, and the repo notes long-running Temporal boundary tests can exceed CI thresholds.
