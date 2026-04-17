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
- Runtime and task fields: runtime, provider profile, model, effort, repository, branch, publish mode, skill, and template state where available

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
- `branch`
- `publishMode`
- `taskInstructions`
- `primarySkill`
- `templateState`

**Validation Rules**

- `taskInstructions` are required for a trustworthy reconstructed draft.
- `branch` is the only active branch field in new draft writes.
- Draft write paths must not require or emit both `startingBranch` and `targetBranch`.
- `publishMode` remains part of the task submission contract, even though the Create page renders it inline with Branch in the Steps card instead of as a separate Execution context card control.
- Other first-slice fields should be filled when available and left as normal form defaults only when that matches existing create-form behavior.
- Draft construction must not mutate source execution data or historical artifacts.

## Legacy Branch Normalization

Older Temporal execution payloads and snapshots may still contain two branch fields. Reconstruction normalizes them into the single authored `branch` field before the form becomes editable.

| Legacy shape | New draft result |
| --- | --- |
| `startingBranch` only | Normalize `startingBranch` to `branch`. |
| `startingBranch == targetBranch` | Normalize the shared value to `branch`. |
| PR publish snapshot with differing `startingBranch` and `targetBranch` | Normalize authored `branch` to old `startingBranch`, because it represented the PR base. Preserve old `targetBranch` only as opaque legacy metadata for audit/debug. |
| Branch publish snapshot with differing `startingBranch` and `targetBranch` | Mark the draft as non-round-trippable under the new model and require a reconstruction warning. Preserve old `targetBranch` only as opaque historical metadata; do not use it for active submission logic. |
| `targetBranch` only | Treat as incomplete legacy metadata and require a reconstruction warning before submit. |

New authored submissions must not reintroduce `targetBranch`; edited or rerun drafts always submit the single `branch` contract.

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
