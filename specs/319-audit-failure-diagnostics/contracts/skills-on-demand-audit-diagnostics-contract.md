# Contract: Skills On Demand Audit and Diagnostics

## Event Types

### `skills_on_demand.query`

Emitted once for every Skills On Demand query attempt.

Required fields:

- `event_type`: `skills_on_demand.query`
- `query_hash`: normalized query hash
- `result_count`: integer count
- `denied`: boolean

Optional bounded fields:

- `workflow_id`
- `run_id`
- `step_id`
- `runtime_id`
- `current_snapshot_id`
- `denial_code`
- `diagnostics_ref`

Forbidden fields:

- raw query text
- full Skill body content
- hidden Skill source paths
- secret values
- arbitrary database or unrestricted artifact access tokens

### `skills_on_demand.request`

Emitted once for every Skills On Demand request attempt.

Required fields:

- `event_type`: `skills_on_demand.request`
- `requested_skills`: normalized requested Skill names
- `result`: `activated`, `denied`, `requires_approval`, or `no_change`

Optional bounded fields:

- `workflow_id`
- `run_id`
- `step_id`
- `runtime_id`
- `parent_snapshot_id`
- `result_code`
- `derived_snapshot_id`
- `manifest_ref`
- `diagnostics_ref`

Forbidden fields:

- full Skill body content
- unrestricted content refs
- hidden source paths
- secret values
- raw long natural-language reason text
- repo-authored `.agents/skills` projection mutation details

## Failure Diagnostic Contract

Failure responses and diagnostic artifacts use stable code/message data.

Required fields:

- `status`: `denied`
- `code`: stable failure code
- `message`: safe human-readable summary

Optional fields:

- `current_snapshot_ref`
- `diagnostics_ref`

Supported failure codes:

- `feature_disabled`
- `unsupported_runtime`
- `invalid_request`
- `snapshot_not_found`
- `skill_not_found`
- `version_not_found`
- `policy_denied`
- `runtime_incompatible`
- `tool_policy_denied`
- `artifact_unavailable`
- `checksum_mismatch`
- `materialization_failed`
- `runtime_refresh_failed`

## Compatibility and Boundary Rules

- Audit events are bounded metadata and must not embed large Skill content.
- Diagnostic artifacts are controlled evidence refs, not arbitrary artifact browsing capability.
- Query text is represented by a hash in event/metric surfaces.
- Request events record normalized Skill names and compact refs only.
- Active snapshot preservation is observable on every denied or failed path.
- `requires_approval` may be represented as a reserved result value but this story does not implement approval workflows.
