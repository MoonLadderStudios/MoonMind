# Data Model: Document Task Snapshot And Compilation Boundary

This story documents task contract metadata and snapshot semantics. It introduces no new persistent storage.

## Authored Preset Binding

Represents a preset relationship authored by the user before control-plane compilation.

Fields:
- `presetId` or `slug`: Stable preset identity.
- `version`: Pinned preset version used for the submitted task.
- `alias`: Include alias when a preset appears within a composition tree.
- `inputMapping`: Authored inputs mapped into the preset.
- `scope`: Visibility scope used for validation and audit.

Validation rules:
- Bindings are resolved and validated before execution-facing payload finalization.
- Bindings are preserved in the task input snapshot even when execution receives only flattened steps.

## Step Source Metadata

Represents how a flattened step entered the submitted task.

Fields:
- `kind`: Manual, preset-derived, included preset step, or detached preset step.
- `presetId` or `slug`: Source preset identity when applicable.
- `version`: Pinned source version when applicable.
- `includePath`: Ordered preset include path when the step came from a composition tree.
- `originalStepId`: Source template step identity when applicable.

Validation rules:
- Manual steps do not require preset identifiers.
- Preset-derived steps must preserve enough source metadata to audit the flattened order.
- Detached steps use `kind: detached` to preserve historical provenance while making clear that live template identity no longer applies.

## Authoritative Task Input Snapshot

Represents the reconstructible submitted task draft used for edit, rerun, audit, and diagnostics.

Fields:
- Objective text and objective-scoped attachments.
- Step text, step-scoped attachments, step identity, and final submitted order.
- Runtime and publish selections.
- Authored preset bindings.
- Include-tree summary.
- Per-step source metadata.
- Detachment state.

Validation rules:
- The snapshot is authoritative for reconstruction; lossy execution projections are not enough.
- Snapshot data must remain meaningful after preset catalog entries are changed, removed, or deactivated.

## Resolved Execution Payload

Represents the worker-facing payload after control-plane compilation.

Fields:
- Fully resolved ordered steps.
- Step instructions and structured inputs needed for execution.
- Optional source metadata that is safe and compact enough to carry across execution boundaries.

Validation rules:
- Runtime workers consume resolved payloads.
- Runtime workers do not expand presets.
- Runtime workers do not depend on live preset catalog correctness for already submitted work.

## State Transitions

1. Draft task contains manual steps, preset references, or composed preset references.
2. Control plane validates and compiles presets.
3. Control plane finalizes a resolved execution payload.
4. Control plane persists the authoritative task input snapshot with authored preset metadata and provenance.
5. Execution plane consumes resolved steps.
6. Edit, rerun, audit, or diagnostics reconstruct authored intent from the snapshot, not from live preset catalog lookup.
