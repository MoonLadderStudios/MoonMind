# Research: Claude Context Snapshots

## Runtime Boundary

Decision: Implement MM-345 as Pydantic schema contracts and deterministic helper functions in `moonmind/schemas/managed_session_models.py`.

Rationale: Existing Claude stories for session core, policy envelopes, decisions, and hooks already use this module as the compact managed-session schema boundary. Context snapshots need the same importable, Temporal-safe validation behavior before workflow or adapter persistence can consume them.

Alternatives considered: A separate `claude_context_models.py` module was considered, but it would split closely related Claude session-plane records and require extra exports before there is enough complexity to justify another module.

## Payload Boundaries

Decision: Store context segment metadata and source pointers only, and validate any extra metadata through the existing compact Temporal mapping helper.

Rationale: The source design requires large payloads to stay out of workflow history and central storage by default. Reusing compact mapping validation keeps behavior consistent with existing managed-session metadata, policy, decision, and hook records.

Alternatives considered: Embedding snippets of file reads or skill bodies was rejected because MM-345 explicitly excludes full transcript central storage and requires payload-light ContextIndex behavior.

## Context Source Kinds

Decision: Model startup and on-demand source kinds as strict literals, with helper tuples exposing the documented startup and on-demand sets for tests and adapter code.

Rationale: The story requires unknown context source kinds to fail instead of silently becoming generic context. Explicit sets also make unit tests cover every documented source kind.

Alternatives considered: Free-form strings plus best-effort normalization were rejected because the project compatibility policy forbids hidden translation layers for internal contracts.

## Reinjection Semantics

Decision: Require every context segment to carry an explicit reinjection policy and provide a deterministic default-policy helper for documented source kinds.

Rationale: Operators must be able to inspect why context survives compaction. A helper gives adapters a single source for the documented defaults while still requiring serialized records to carry the explicit policy.

Alternatives considered: Inferring the policy only at read time was rejected because it would make historical snapshots less self-describing and harder to audit.

## Compaction Epochs

Decision: Provide a helper that creates a new snapshot with an incremented epoch, copies only reinjectable segments according to policy, emits a compaction work item, and emits bounded context events.

Rationale: The acceptance criteria require compaction to create a new ContextSnapshot epoch rather than mutating the old one and to emit visible work/event evidence. A deterministic helper makes this behavior testable without live Claude execution.

Alternatives considered: Leaving compaction entirely to future workflow code was rejected because MM-345 needs boundary behavior that can be validated independently.

## Testing Strategy

Decision: Add focused unit tests for model validation, policy defaults, guidance classification, immutable compaction behavior, and compact metadata limits; add an integration-style schema boundary test for representative startup, on-demand, compaction work, and event flow.

Rationale: This matches the existing Claude policy and decision story pattern and gives workflow/adapters a stable contract before persistence or runtime wiring is added.

Alternatives considered: Full Temporal workflow tests were rejected for this story because no workflow or activity signature changes are required; integration-style schema tests are sufficient for the current boundary.
