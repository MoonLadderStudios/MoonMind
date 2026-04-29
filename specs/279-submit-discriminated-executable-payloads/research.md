# Research: Submit Discriminated Executable Payloads

## Decision 1: Validate Step Type at the task contract boundary

Decision: Add explicit submitted-step validation in `moonmind/workflows/tasks/task_contract.py`.

Rationale: The task contract is the earliest shared backend boundary for task-shaped execution payloads. It can reject unresolved Preset steps, Activity labels, and conflicting Tool/Skill payloads before any workflow history or runtime plan nodes are produced.

Alternatives considered: Only relying on the Create-page UI was rejected because API and automation callers can submit task payloads directly.

## Decision 2: Preserve legacy step fields only when no explicit Step Type is supplied

Decision: Explicit `type` values are validated strictly. Existing steps without `type` continue through current selection rules while new submitted payloads include `type`.

Rationale: The source design acknowledges migration readers, but unsupported explicit runtime values must fail fast. This keeps existing stored drafts readable without accepting ambiguous new discriminators.

## Decision 3: Use explicit Step Type for runtime plan node selection

Decision: In `worker_runtime.py`, explicit Tool steps materialize as typed tool plan nodes using the submitted tool id/name; explicit Skill steps materialize through the existing agent-runtime or Jira skill path.

Rationale: This directly satisfies runtime mapping semantics without changing the plan executor's Temporal Activity internals.

## Decision 4: Keep preset provenance audit-only

Decision: Preserve `source` and existing `presetProvenance` metadata in step inputs, but do not require either to choose the runtime node.

Rationale: The source design treats provenance as audit and reconstruction metadata, not hidden runtime work.

## Decision 5: Focus integration evidence on materializer unit tests

Decision: Use Python unit tests for payload validation and materialization plus Vitest for browser submission payloads. Do not add compose-backed integration tests for this story.

Rationale: No database, external service, or worker-topology behavior changes are needed. The contract boundary can be exercised deterministically in unit tests.
