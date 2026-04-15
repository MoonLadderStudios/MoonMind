# Contract: Temporal Boundary Inventory

## Public Module

`moonmind.workflows.temporal.boundary_inventory`

## Functions

### `get_temporal_boundary_inventory() -> TemporalBoundaryInventory`

Returns the deterministic MM-327 boundary inventory.

Requirements:
- Returns `sourceIssueKey = "MM-327"` and `boardScope = "TOOL"`.
- Returns at least one contract for each covered kind needed by the story: activity, workflow, signal, update, query, and Continue-As-New.
- Does not inspect external services, databases, credentials, or runtime history.
- Does not mutate global state.

### `iter_temporal_boundary_contracts() -> tuple[TemporalBoundaryContract, ...]`

Returns the inventory contracts as an immutable tuple for tests and review gates.

Requirements:
- Preserves deterministic ordering.
- Contract names remain stable unless an explicit Temporal migration plan exists.

## Schema Module

`moonmind.schemas.temporal_boundary_models`

Required models:
- `TemporalBoundaryModelRef`
- `TemporalBoundaryContract`
- `TemporalBoundaryInventory`

Model requirements:
- Use Pydantic v2.
- Use camelCase aliases for wire shape.
- Reject unknown extra fields.
- Normalize nonblank identifiers.
- Use closed literals for boundary kind, model role, and contract status.

## Validation Surface

Tests must confirm:
- Unknown fields are rejected.
- Blank identifiers are rejected.
- Covered activity names exist in `DEFAULT_ACTIVITY_CATALOG`.
- Covered workflow/message names match known constants or workflow declarations.
- `MM-327` and `TOOL` are preserved.
