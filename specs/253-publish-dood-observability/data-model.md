# Data Model: Publish Durable DooD Observability Outputs

## Entities

### Docker-Backed Workload Publication Record

Represents one Docker-backed workload result published for operator inspection.

Fields:
- `request_id`
- `profile_id`
- `status`
- `exit_code`
- `started_at`
- `completed_at`
- `duration_seconds`
- optional `timeout_reason`
- bounded ownership labels

Rules:
- The publication record is durable through artifact refs and bounded metadata, not through transient daemon state.
- Success and failure paths both preserve an inspectable publication record.

### Durable Observability Evidence Set

Represents the operator-visible artifact set produced by one workload run.

Fields:
- optional `runtime.stdout`
- optional `runtime.stderr`
- optional `runtime.diagnostics`
- optional declared output refs such as `output.primary`, `output.summary`, and tool-specific outputs
- `artifactPublication` status payload
- optional `reportPublication` status payload

Validation rules:
- Runtime log and diagnostics artifacts are redacted before publication.
- Declared output refs remain bounded to MoonMind-owned artifact paths.
- Partial publication failure preserves available refs and records bounded publication errors.

### Bounded Audit Metadata

Represents the metadata attached to a Docker-backed workload result for audit and observability.

Fields:
- `dockerMode`
- `workloadAccess`
- optional `unrestrictedContainer`
- optional `unrestrictedDocker`
- optional `profileId`
- optional `image` or `imageRef`
- `artifactPublication`
- optional `reportPublication`
- optional normalized or redacted `dockerHost`
- timing and status fields

Rules:
- Metadata must make unrestricted execution obvious when it occurs.
- Secret-looking values are redacted before publication.
- Docker host details are normalized or redacted before publication.

### Shared Report Publication Outcome

Represents the publication state for a Docker-backed workload that declares a primary report.

Fields:
- declared primary output ref
- optional summary output ref
- optional report publication metadata
- artifact publication metadata

Rules:
- Declared report outputs use the same publication semantics across supported Docker-backed launch types.
- Report publication remains artifact-backed rather than inline in workflow-visible state.

### Operator Inspection View

Represents the data available to operators when inspecting a completed Docker-backed workload.

Fields:
- durable artifact refs
- bounded audit metadata
- execution identity and labels
- optional redaction level or visibility constraints from the artifact system

Rules:
- The inspection view must remain usable after the container and daemon-local state are gone.
- Operators do not need terminal scrollback or container-local history as the source of truth.

## Relationships

- One `Docker-Backed Workload Publication Record` produces one `Durable Observability Evidence Set`.
- `Durable Observability Evidence Set` is summarized by `Bounded Audit Metadata`.
- `Shared Report Publication Outcome` is an optional subset of the evidence set when a primary report is declared.
- `Operator Inspection View` reads the evidence set and audit metadata without mutating published artifacts.

## Validation Notes

- Partial artifact publication failures remain bounded and visible through `artifactPublication` error details.
- Artifact classes for runtime logs, diagnostics, summaries, and reports remain stable across supported Docker-backed launch types.
- Secret-looking content must be redacted before artifact or metadata publication, not only during UI rendering.
