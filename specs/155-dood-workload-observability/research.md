# Research: DooD Workload Observability

## Decision: Publish workload streams and diagnostics as durable artifacts during launcher finalization

**Rationale**: The launcher is the component that has direct access to process stdout, stderr, exit status, timeout/cancel state, selected profile, image reference, container identity, and cleanup outcome. Publishing at this boundary gives every finalized run durable evidence before the result is converted into an executable tool response or step projection.

**Alternatives considered**:

- Publish only in the tool bridge: rejected because direct `workload.run` activity callers would not get the same artifact behavior.
- Publish only in task-detail projection: rejected because projection should consume evidence, not reconstruct it from bounded metadata.
- Leave streams embedded in result metadata: rejected because large operational output must not become durable truth inside workflow payloads.

## Decision: Keep workflow/tool results bounded and artifact-reference-first

**Rationale**: Phase 4 requires successful and failed runs to be diagnosable from artifacts alone. The result payload should carry references, status, and compact metadata rather than full logs, preserving workflow-history size and making UI/API consumers use the artifact system for detailed evidence.

**Alternatives considered**:

- Return full stdout/stderr in tool outputs: rejected because output can be large and would duplicate artifact truth.
- Store only a combined log artifact: rejected because separate stdout, stderr, and diagnostics are required for operator diagnosis and stream-specific presentation.
- Store only diagnostics and omit streams: rejected because operators often need raw process output to diagnose workload failures.

## Decision: Model declared outputs as artifact classes linked to paths under the workload artifacts directory

**Rationale**: Workloads can produce domain outputs such as reports, summaries, packages, or primary artifacts. A declaration map lets callers identify expected outputs while keeping path authority bounded to the workload artifact directory. Missing declared outputs can be reported without losing available runtime evidence.

**Alternatives considered**:

- Auto-discover all files under the artifacts directory: rejected because it can over-publish transient or sensitive files and makes output contracts implicit.
- Allow absolute output paths: rejected because outputs must stay within the approved artifact location.
- Treat missing declared outputs as artifact publication failure: rejected because a workload may fail before producing a report while stdout/stderr/diagnostics remain valuable.

## Decision: Preserve session association as metadata only

**Rationale**: A workload may be launched from a managed-session-assisted step, but the workload container is still an executable tool workload. Session id, epoch, and source turn id are useful grouping fields, while session continuity artifacts remain owned by the managed session plane.

**Alternatives considered**:

- Publish workload logs as session continuity artifacts: rejected because it blurs session identity and violates the DooD boundary.
- Omit session association entirely: rejected because operators need to understand which session turn requested a workload.
- Create a managed-agent run for each workload: rejected because Phase 4 targets ordinary one-shot executable tools, not true managed agent runtimes.

## Decision: Surface workload evidence through existing execution detail and task detail projections

**Rationale**: Operators already use task/detail views and artifact tables to inspect execution evidence. Workload metadata should appear as step-owned output metadata and artifact references rather than a separate workload-only page unless later usage proves that a dedicated view is needed.

**Alternatives considered**:

- Add a new workload detail route immediately: rejected as unnecessary for Phase 4 and likely to duplicate existing artifact/task views.
- Hide workload evidence behind raw artifact listings only: rejected because operators need step linkage and status context.
- Present workload containers as session records: rejected because it implies session identity where none exists.

## Decision: Validation focuses on unit and workflow-boundary tests first

**Rationale**: The highest-risk behavior is contract shape, artifact publication, step linkage, session boundary preservation, and failure/timeout metadata. Unit and workflow-boundary tests can cover these deterministically without requiring Docker daemon availability in managed agent workspaces.

**Alternatives considered**:

- Require compose-backed Docker integration tests for this phase gate: rejected for the unit gate because managed agent jobs may not have Docker socket access; integration coverage can follow where the local stack supports it.
- Rely on manual UI inspection: rejected because artifact and metadata contracts need automated regression coverage.
- Test only the launcher: rejected because the tool result and task/detail projections are part of the Phase 4 acceptance surface.
