# Research: Step Ledger Phase 1

## Decision 1: Keep step ledger state in a workflow-local pure helper module

- **Decision**: Introduce a pure Python helper module dedicated to compact step-ledger state, transitions, and progress reduction, then have `MoonMind.Run` call it.
- **Rationale**: The existing workflow currently spreads coarse summary state across `_summary`, `_waiting_reason`, `_attention_required`, and `_step_count`. A pure helper isolates bounded state rules from workflow dispatch logic and makes TDD practical without starting Temporal.
- **Alternatives considered**:
  - Store the ledger directly as ad hoc dict mutations inside `run.py`: rejected because the state machine would become harder to validate and reason about.
  - Add full ledger models only in API/router code: rejected because the workflow must own truth before any API phase.

## Decision 2: Use workflow queries for ledger and progress reads

- **Decision**: Expose latest-run progress and step-ledger state through workflow queries and keep those queries readable after workflow completion.
- **Rationale**: Queries do not add event-history noise and align with the canonical design in `docs/Temporal/StepLedgerAndProgressModel.md`.
- **Alternatives considered**:
  - Use Memo/Search Attributes as the source of truth: rejected because they are intentionally bounded and lossy.
  - Materialize a projection first: rejected for this phase because workflow-owned truth is the primary deliverable.

## Decision 3: Mirror only compact operator-visible summary data into Memo/Search Attributes

- **Decision**: Continue using Memo/Search Attributes for compact summary state only, and do not embed step rows, attempts, or checks there.
- **Rationale**: This preserves current operational surfaces while keeping Temporal payload budgets healthy and aligns with the normative doc.
- **Alternatives considered**:
  - Store the full ledger in Memo for easy UI reads: rejected because it violates the canonical design and risks bloating payloads.
  - Store per-step counts in Search Attributes: rejected because step-row state is not a visibility index.

## Decision 4: Freeze step-ledger and progress shape now via schema models plus golden fixtures

- **Decision**: Add canonical schema models and golden fixture coverage for `progress` and representative step rows before wiring the workflow implementation.
- **Rationale**: Phase 0 is explicitly a contract freeze. Golden fixtures provide a stable review surface and prevent field drift before API/UI work begins.
- **Alternatives considered**:
  - Defer schema models until the API phase: rejected because that would let workflow code invent private shapes that later consumers must adapt to.

## Decision 5: Keep evidence slots as structured placeholders in Phase 1

- **Decision**: Include stable `checks`, `refs`, and `artifacts` fields in every row now, but allow them to remain empty/default until Phase 2 evidence wiring is implemented.
- **Rationale**: The user asked to freeze the v1 contract first. Stable placeholders let later phases add data without changing the shape.
- **Alternatives considered**:
  - Omit fields until populated: rejected because it would force later breaking schema changes.
