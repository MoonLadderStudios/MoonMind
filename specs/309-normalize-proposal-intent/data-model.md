# Data Model: Normalize Proposal Intent in Temporal Submissions

## Canonical Task Proposal Intent

Represents proposal behavior requested by a submitted task.

Fields:
- `task.proposeTasks`: boolean task-level opt-in for proposal generation.
- `task.proposalPolicy`: optional policy object controlling proposal targets, caps, severity floor, and default runtime for generated proposals.

Validation rules:
- New task submissions write proposal intent only under `task`.
- `task.proposeTasks` is boolean.
- `task.proposalPolicy.targets` entries are limited to supported proposal targets.
- `task.proposalPolicy.maxItems` is an object keyed by supported proposal destinations.
- Runtime values inside proposal policy follow the existing task runtime validation rules.

Relationships:
- Stored as part of the run's initial task payload.
- Consumed by the Temporal proposal stage and proposal submission activities.
- Preserved through proposal promotion and managed-session task creation.

## Compatibility Proposal Intent Read

Represents older persisted proposal intent that may appear outside the canonical nested task payload in in-flight or replayed workflow history.

Fields:
- root-level `proposeTasks` from older run parameters.
- older flattened proposal policy hints, read only where current code already supports replay-safe behavior.

Validation rules:
- Compatibility reads must not become new write output.
- New submissions must not emit root-level proposal intent.
- Compatibility reads must be covered by workflow-boundary regression tests.

Relationships:
- Only used by workflow compatibility gates.
- Not exposed as the durable contract for new task creation.

## Proposal-Capable Run State

Represents the lifecycle state visible while proposal generation or delivery is in progress.

Fields:
- `proposals`: workflow/API/UI vocabulary value for proposal-stage execution.
- proposal summary metadata: requested, generated count, submitted count, and errors.

Validation rules:
- The state value remains consistent across workflow state, API responses, Mission Control mapping, finish summaries, and touched documentation.
- Summary metadata must not include raw credentials or unredacted external tokens.

Relationships:
- Emitted by `MoonMind.Run`.
- Projected through API and Mission Control status mapping.
- Included in run finish summaries.

## State Transitions

- `executing` -> `proposals` when global proposal generation is enabled and canonical `task.proposeTasks` is true.
- `executing` -> `finalizing` when proposal generation is globally disabled or canonical `task.proposeTasks` is false.
- `proposals` -> `finalizing` after best-effort proposal generation/submission completes or records a non-fatal proposal error.
