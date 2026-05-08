# Data Model: Prepare Target-Aware Inputs

Traceability: Jira issue `MM-631`; this artifact supports the original Jira preset brief preserved in `spec.md`.

## Input Attachment Ref

Represents one authored structured input from the canonical task payload.

Fields:
- `artifactId`: Stable artifact identifier for source bytes.
- `filename`: Original display filename.
- `contentType`: MIME type.
- `sizeBytes`: Non-negative source size.
- `targetKind`: Derived from containing field, either `objective` for `task.inputAttachments` or `step` for `task.steps[].inputAttachments`.
- `stepRef`: Required for step-scoped refs after normalization; absent for objective-scoped refs.
- `stepOrdinal`: Step position used only as bounded metadata and fallback traceability.

Validation rules:
- Entries must not contain embedded binary, base64, data URLs, or generated markdown.
- Objective refs must come only from `task.inputAttachments`.
- Step refs must come only from the owning step's `inputAttachments`.

## Prepared Input Manifest

Canonical preparation output for an attachment-aware runtime execution.

Fields:
- `version`: Manifest schema version.
- `attachments`: Ordered prepared input entries.
- `manifestRef` or `manifestPath`: Bounded reference to the manifest artifact or workspace-local manifest.
- `diagnostics`: Bounded preparation events suitable for operator troubleshooting.

Validation rules:
- Every valid authored attachment needed for execution has exactly one manifest entry.
- Entries preserve target kind and step reference from the authored task contract.
- Manifest body may be artifact-backed or workspace-local, but workflow history carries only compact refs/metadata.

## Prepared Input Entry

Represents one materialized source input.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`
- `targetKind`
- `stepRef` when target kind is `step`
- `stepOrdinal` when target kind is `step`
- `workspacePath` or equivalent raw input ref
- `derivedContextRef` or `derivedContextPath` when image context exists
- `status`: `prepared`, `skipped`, or `failed`

Validation rules:
- Raw file content is never embedded inline in workflow state.
- Derived image context is secondary context and never mutates instruction text.
- Failed entries include bounded reason metadata and prevent affected step dispatch.

## Step Prepared Context

Adapter-visible filtered context for one logical step.

Fields:
- `logicalStepId`
- `objectiveContextRefs`: Prepared objective entries relevant to the step.
- `stepContextRefs`: Prepared entries whose `stepRef` matches the represented step.
- `manifestRef`: Reference to the canonical manifest.
- `diagnosticRefs`: Optional bounded diagnostic refs.

Validation rules:
- Step context must not contain entries for unrelated step refs.
- Objective context may be included for each step by default.
- Child `AgentRun` requests receive only the `Step Prepared Context` for the represented step.

## State Transitions

1. Authored task submitted with structured attachment refs.
2. Runtime prepare starts before affected step execution.
3. Prepare materializes raw files and generates secondary image context.
4. Prepare writes manifest and records bounded refs/diagnostics.
5. For each step, the workflow filters prepared context to objective plus represented step.
6. Step or child `AgentRun` executes with only filtered prepared context.
7. Prepare failure stops affected execution before dispatch and exposes an operator-readable reason.
