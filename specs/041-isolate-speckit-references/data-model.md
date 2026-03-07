# Data Model: Isolate Spec Kit References and Skill-First Runtime

## Entity: SkillAdapterBinding

- **Description**: Maps a workflow skill identifier to a concrete adapter implementation identifier.
- **Fields**:
  - `skill_name` (string): selected skill ID (for example `speckit`, `custom`).
  - `adapter_id` (string): adapter implementation key resolved from registry.
  - `supports_stage` (set[string]): stage names supported by the adapter.
- **Rules**:
  - Missing binding for selected skill is a hard error.
  - Binding lookup must be deterministic and side-effect free.

## Entity: WorkflowDependencyCheck

- **Description**: Runtime preflight decision for which external CLI dependencies are required for current execution context.
- **Fields**:
  - `selected_skills` (set[string]): effective skills in scope for startup/task execution.
  - `requires_speckit` (bool): true only when selected skills include `speckit`.
  - `missing_dependencies` (list[string]): required dependencies that failed verification.
- **Rules**:
  - Non-speckit-only contexts must not require Speckit.
  - Any missing required dependency fails preflight with actionable error message.

## Entity: WorkflowRouteAliasUsage

- **Description**: Captures use of deprecated workflow API aliases.
- **Fields**:
  - `request_path` (string): invoked route path.
  - `alias_type` (string): deprecated alias class (`legacy_speckit_prefix`).
  - `timestamp` (datetime): request handling time.
  - `run_id` (UUID | null): optional run identifier extracted from route params.
- **Rules**:
  - Legacy alias calls emit deprecation header and structured log entry.
  - Canonical routes do not emit alias usage records.

## Entity: WorkflowRunPersistence (existing)

- **Description**: Existing persisted workflow run and task state records in SPEC-prefixed tables.
- **Fields**: unchanged in this feature.
- **Rules**:
  - Storage schema remains unchanged.
  - Canonical and legacy API routes both serialize from the same persisted records.
