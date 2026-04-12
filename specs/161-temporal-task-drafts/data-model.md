# Data Model: Temporal Task Draft Reconstruction

## Task Submit Page Mode

Represents the operator's intent for the shared submit page.

**Fields**

- `mode`: one of `create`, `edit`, or `rerun`
- `executionId`: workflow identifier when mode is `edit` or `rerun`; absent in create mode

**Validation Rules**

- Rerun identifier wins when both edit and rerun identifiers are present.
- Edit mode requires an edit execution identifier.
- Rerun mode requires a rerun execution identifier.
- Create mode must not trigger execution-detail loading.

## Temporal Source Execution

Existing Temporal execution used to reconstruct the form draft.

**Fields**

- `workflowId`: unique workflow identity
- `workflowType`: supported workflow type; Phase 2 supports only `MoonMind.Run`
- `state` / lifecycle fields: operator-visible lifecycle state
- `actions`: capability set from backend
- `inputParameters`: structured task input state
- `inputArtifactRef`: optional immutable artifact reference for historical input state
- Runtime and task fields: runtime, provider profile, model, effort, repository, branches, publish mode, skill, and template state where available

**Validation Rules**

- `workflowType` must be `MoonMind.Run`.
- Edit mode requires `actions.canUpdateInputs = true`.
- Rerun mode requires `actions.canRerun = true`.
- Missing or malformed source data must produce an explicit error rather than a submit-ready partial draft.

## Task Editing Capability Set

Backend-owned action capability data used by the submit page.

**Fields**

- `canUpdateInputs`: whether active in-place input updates may be requested later
- `canRerun`: whether a terminal execution may request a rerun later
- `disabledReasons`: optional machine-readable reasons for unsupported actions

**Validation Rules**

- Capability flags are authoritative for edit/rerun display.
- Frontend lifecycle guesses must not override missing capability flags.
- Missing capability is treated as false for the requested mode.

## Temporal Submission Draft

The shared form state reconstructed from a Temporal source execution and optional input artifact content.

**Fields**

- `runtime`
- `providerProfile`
- `model`
- `effort`
- `repository`
- `startingBranch`
- `targetBranch`
- `publishMode`
- `taskInstructions`
- `primarySkill`
- `templateState`

**Validation Rules**

- `taskInstructions` are required for a trustworthy reconstructed draft.
- Other first-slice fields should be filled when available and left as normal form defaults only when that matches existing create-form behavior.
- Draft construction must not mutate source execution data or historical artifacts.

## Input Artifact Reference

Immutable historical reference to task input content.

**Fields**

- `artifactId`: artifact identity
- `content`: JSON-compatible task input content when read successfully

**Validation Rules**

- Read only; never mutate in Phase 2.
- Only read when inline instructions are absent or insufficient.
- Unreadable or malformed content produces an explicit reconstruction error.

## Template State

Prior applied template data restored into the reviewable draft.

**Fields**

- `slug`
- `version`
- `inputs`
- `stepIds`
- `appliedAt`
- `capabilities`

**Validation Rules**

- Template entries without a stable template identity are ignored rather than inventing a template state.
- Template state is used for review/prefill only in Phase 2.
