# Contract: DooD Observability Publication

## Purpose

Define the runtime-visible contract for MM-504 so every Docker-backed workload publishes durable observability evidence, bounded audit metadata, and shared report-publication outcomes that operators can inspect without daemon-local state.

## Publication Boundary

Docker-backed workload publication is owned by MoonMind workload launch and artifact services.

Inputs to the boundary include:
- execution identity and ownership labels
- workload status and exit metadata
- stdout and stderr streams
- diagnostics payload
- declared output paths
- bounded workload metadata such as mode, access class, image, timing, and publication status

Rules:
- Publication writes durable artifacts and bounded metadata rather than relying on container-local state.
- Publication may succeed partially, but bounded publication status and error details must still be recorded.
- Shared report publication semantics apply when a workload declares a primary report.

## Minimum Durable Outputs

Each supported Docker-backed workload publication must preserve, where available:
- `runtime.stdout`
- `runtime.stderr`
- `runtime.diagnostics`
- declared outputs such as `output.summary`, `output.primary`, and tool-specific report/log outputs
- bounded `artifactPublication` status metadata
- bounded `reportPublication` status metadata when report publication is requested

Rules:
- Success and failure paths both preserve durable evidence.
- Missing declared outputs are recorded as bounded publication metadata rather than silently discarded.

## Audit Metadata Contract

Published workload metadata must provide a bounded operator-facing record that can include:
- `workflowDockerMode`
- `workloadAccess`
- explicit unrestricted indicators when relevant
- `profileId`
- image reference information
- normalized or redacted `dockerHost`
- timing, duration, status, and exit code fields
- publication status fields

Rules:
- Unrestricted execution must be obvious from published metadata.
- Raw secret-like values are never published in metadata.
- Docker host details are normalized or redacted before publication.

## Redaction Contract

Redaction behavior applies before publication to:
- stdout
- stderr
- diagnostics payloads
- top-level workload metadata
- any secret-like values surfaced through publication status details

Rules:
- Redaction happens before durable publication, not only at read time.
- Redaction preserves bounded operator usefulness while removing raw secret-like content.

## Artifact-Class Consistency

Supported Docker-backed launch types must preserve consistent observability expectations for:
- runtime log artifacts
- runtime diagnostics artifacts
- human-readable summaries
- declared primary reports when present
- publication status metadata

Rules:
- Artifact classes remain stable across supported launch types.
- Generic report and runtime artifact classes stay compatible with the existing artifact/report contract helpers.

## Operator Inspection Contract

Operators inspect Docker-backed workload outcomes through MoonMind’s stored artifacts and bounded metadata.

Rules:
- Container-local history, daemon state, and terminal scrollback are not the source of truth.
- Stored artifacts and metadata remain sufficient for post-run diagnosis.
- Inspection surfaces consume durable refs and bounded metadata without mutating prior publication records.

## Verification Targets

This contract is satisfied when tests prove:
- representative Docker-backed workload paths publish the minimum durable outputs
- declared primary reports follow the shared publication contract when configured
- workload metadata exposes mode and access information with explicit unrestricted markers when relevant
- docker host details and secret-like values are normalized or redacted before publication
- operators can inspect stored workload artifacts without daemon-local state
- supported Docker-backed launch types retain consistent artifact classes and observability expectations
