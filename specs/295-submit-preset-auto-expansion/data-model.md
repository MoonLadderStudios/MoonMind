# Data Model: Submit Preset Auto-Expansion

This story uses existing task, Preset, artifact, and execution records. No new persistent storage is planned.

## Create-page Draft

Represents the visible authoring state before submission.

Fields:
- `objectiveText`: task-level objective text.
- `objectiveAttachments`: structured attachment refs or local files for the task objective.
- `steps`: ordered `StepDraft` entries.
- `repository`, `branch`, `publishMode`, `mergeAutomationEnabled`: authored repository and publishing context.
- `runtime`, `providerProfile`, `model`, `effort`: authored execution context.
- `dependencies`, `schedule`: authored run controls.

Validation rules:
- Exactly one visible Step Type per step: `tool`, `skill`, or `preset`.
- Shared fields can survive Step Type changes.
- Incompatible type-specific fields cannot silently reach final submission.
- Non-submit interactions cannot create or update tasks.

## Step Draft

Represents one authored step in the visible draft.

Fields:
- `localId`: browser-local stable identity for mapping attachments and UI state.
- `id`, `title`, `instructions`: common authored step fields.
- `stepType`: `tool`, `skill`, or `preset`.
- `inputAttachments`: persisted structured refs for step attachments.
- `tool`, `skill`, `preset`: type-specific draft payloads.
- `source`: optional provenance for generated executable steps.

Validation rules:
- A `preset` step is authoring-only and cannot be present in final task execution payloads.
- A `tool` final step must carry a Tool payload and no Skill payload.
- A `skill` final step must carry a Skill payload and no non-skill Tool payload.
- Generated executable steps may be edited like authored executable steps after manual Apply.

## Preset Draft

Represents one unresolved authoring-time Preset step.

Fields:
- `key`: selected catalog key or scoped selector.
- `version`: selected version when explicitly chosen.
- `inputValues`: user-provided Preset input values.
- `detail`: descriptor data when loaded.
- `preview`: manual preview state.
- `submitExpansion`: transient submit-time expansion status.
- `message`: local Preset-specific feedback.

Validation rules:
- `key` must be authored by the user through Preset selection; the browser must not infer a different key.
- If `version` is absent, the catalog resolves the latest active user-visible version or returns a validation error.
- Required Preset inputs must validate before expansion.
- `submitExpansion` is transient UI state and is never written to the final task snapshot.

## Preset Submit Expansion State

Represents user-visible progress for one submit attempt.

Fields:
- `status`: `idle`, `queued`, `expanding`, `expanded`, or `failed`.
- `requestId`: identity for the active submit attempt.
- `message`: progress or non-blocking warning copy.
- `errorMessage`: blocking validation, authorization, ambiguity, or expansion failure.

Validation rules:
- Stale `requestId` results must be ignored.
- Failed state blocks final submission and preserves the visible draft.
- Status can be reset by editing relevant Preset inputs or starting a new submit attempt.

## Frozen Submission Copy

Represents a submit-attempt-local copy of the visible draft.

Fields:
- `steps`: ordered copy of draft steps before expansion.
- `attachments`: structured refs available to expansion and final payload construction.
- `taskContext`: repository, branch, publish mode, runtime, and other task-level context.
- `submitIntent`: create, update, or rerun.

Validation rules:
- It is created only after an explicit primary submit click.
- Preset placeholders are replaced in authored order.
- The visible draft is not silently overwritten by this copy.
- Final validation rejects any remaining unresolved Preset step.

## Generated Executable Step

Represents a Tool or Skill step returned by Preset expansion.

Fields:
- `id`, `title`, `instructions`: generated step identity and text.
- `type`: `tool` or `skill`.
- `tool` or `skill`: executable payload.
- `inputAttachments`: structured attachment refs if mapped unambiguously.
- `source`: optional Preset provenance.

Validation rules:
- Must be contract-equivalent to manual Apply output.
- Must preserve provided provenance.
- Must not require live Preset lookup at runtime.
- Must apply warnings, capabilities, attachment mappings, and publish/merge constraints before final payload validation.

## State Transitions

```text
visible draft with unresolved Presets
  -> explicit submit click
  -> guarded submit state
  -> frozen submission copy
  -> queued/expanding each unresolved Preset in authored order
  -> expanded executable submission copy
  -> executable-only validation
  -> normal create/update/rerun submit
```

Failure transitions:
- Expansion unavailable, unauthorized, invalid, ambiguous, stale, cancelled, or failed -> mark relevant Preset failed, block final submit, preserve visible draft.
- Expansion succeeds but final submit fails -> show final submission failure, preserve original Preset draft, optionally expose expanded copy for review.
- Duplicate click during guarded state -> ignore duplicate or keep it attached to the same request; create no duplicate side effect.
