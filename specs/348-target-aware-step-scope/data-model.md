# Data Model: Target-Aware Step Execution Scope

## Prepared Context Item

- `artifactId`: Stable artifact identifier for the original input.
- `targetKind`: `objective` or `step`.
- `stepRef`: Required when `targetKind` is `step`; forbidden for `objective`.
- `rawInputRef`: Lightweight artifact ref for the prepared raw input.
- `derivedContextRef`: Optional lightweight ref for generated context derived from the input.
- `workspacePath`: Optional stable materialization path for diagnostics and runtime adapters.
- `status`: Preparation status, expected to be `prepared` before step dispatch.

Validation:
- Step items without `stepRef` are invalid.
- Objective items with `stepRef` are invalid.
- Inline binary data, data URLs, generated markdown bodies, and secret-like values are invalid for workflow-visible metadata.

## Prepared Input Manifest

- `manifestRef`: Lightweight ref identifying the prepared-input manifest.
- `entries`: Ordered collection of Prepared Context Items.

Relationships:
- One task execution has zero or one active prepared-input manifest.
- A manifest may contain objective entries and entries for multiple steps.
- Runtime context selection reads the manifest but never mutates target binding.

## Step Runtime Context

- `logicalStepId`: Stable logical step identifier for the current runtime step.
- `manifestRef`: Ref to the manifest used for selection.
- `objectiveContextRefs`: Prepared refs visible to the step because they are task-level objective context.
- `stepContextRefs`: Prepared refs visible to the step because they are explicitly bound to `logicalStepId`.
- `rawInputRefs`: Raw artifact refs for the objective and current-step items only.
- `inputRefs`: Deduplicated effective refs delivered to non-managed runtime adapters or metadata for managed runtimes.

Validation:
- `stepContextRefs` must contain only entries whose `stepRef` equals `logicalStepId`.
- Refs for unrelated steps must be absent.
- Large or binary content must never be embedded.

## AgentRun Child Input

- `AgentExecutionRequest.inputRefs`: Effective compact refs for external/coordinated runtimes, excluding unrelated step refs.
- `AgentExecutionRequest.parameters.metadata.moonmind.preparedContext`: Parent-owned prepared-context metadata for managed and external child runs.
- `AgentExecutionRequest.parameters.metadata.moonmind.stepLedger`: Optional step ledger context for the represented step.

Validation:
- Parent MoonMind.Run is the only authority that selects prepared context for the represented step.
- Child workflows may consume and report the parent-provided prepared context but must not redefine target binding.
- Child diagnostics may include bounded refs/counts, not unrelated attachment content.

## State Transitions

1. Task payload contains objective and step-scoped attachment refs.
2. Prepare produces a manifest with explicit target metadata.
3. Parent workflow selects one Step Runtime Context for a logical step.
4. Parent workflow dispatches runtime or AgentRun child input with only objective plus current-step context.
5. Child result and diagnostics preserve parent-owned target-binding metadata without broadening scope.
