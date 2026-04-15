# Data Model: Temporal Boundary Models

## TemporalBoundaryModelRef

Represents one named Pydantic model that owns a Temporal request, response, snapshot, or continuation shape.

Fields:
- `module`: dotted Python module path.
- `name`: concrete model class name.
- `role`: one of `request`, `response`, `snapshot`, `continuation`, or `metadata`.
- `schema_home`: one of the approved schema homes or a precise domain schema module.

Validation rules:
- `module`, `name`, and `schema_home` must be nonblank after trimming.
- `role` is a closed value.
- Extra fields are rejected.

## TemporalBoundaryContract

Represents one public Temporal boundary in the deterministic inventory.

Fields:
- `kind`: one of `workflow`, `activity`, `signal`, `update`, `query`, or `continue_as_new`.
- `name`: stable Temporal boundary name.
- `owner`: workflow or activity family that owns the boundary.
- `request_model`: required `TemporalBoundaryModelRef`.
- `response_model`: optional `TemporalBoundaryModelRef`.
- `status`: one of `modeled`, `compatibility_shim`, or `tracking_only`.
- `schema_home`: approved schema home or domain schema module rationale.
- `coverage_ids`: nonempty list of source design IDs.
- `rationale`: required when `status` is not `modeled` or when no response model applies.
- `metadata_fields`: bounded metadata field names when the boundary intentionally carries annotation metadata.

Validation rules:
- `name`, `owner`, and `schema_home` must be nonblank after trimming.
- `coverage_ids` must be nonempty and each value must be nonblank.
- `rationale` is required for `compatibility_shim` and `tracking_only`.
- Extra fields are rejected.

## TemporalBoundaryInventory

Groups the deterministic inventory for review and tests.

Fields:
- `source_issue_key`: Jira issue key preserved from MM-327.
- `board_scope`: Jira board scope preserved from TOOL.
- `contracts`: nonempty list of `TemporalBoundaryContract`.

Validation rules:
- `source_issue_key` and `board_scope` must be nonblank after trimming.
- Contract names must be unique by `(kind, name, owner)`.
- Extra fields are rejected.

## State Transitions

Boundary status can only move forward by explicit code review:
- `tracking_only` -> `compatibility_shim` when a named compatibility model exists.
- `compatibility_shim` -> `modeled` when the canonical typed request/response is the public boundary.
- `modeled` must not move backward unless a new compatibility issue is documented under `docs/tmp/`.
