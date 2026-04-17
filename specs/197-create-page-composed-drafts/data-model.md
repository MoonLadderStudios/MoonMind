# Data Model: Create Page Composed Preset Drafts

## AppliedPresetBinding

Represents one applied preset composition in the browser draft.

Fields:

- `bindingId`: stable browser-local identifier for the applied binding.
- `presetSlug`: applied preset slug.
- `presetVersion`: pinned preset version used for expansion.
- `scope`: preset visibility scope when available.
- `includePath`: ordered include aliases from the root preset to the source preset.
- `rootPresetSlug`: root preset slug for the expansion.
- `expansionDigest`: digest returned by the server for the expanded composition.
- `groupLabel`: user-facing grouped preview label.
- `status`: `bound`, `needs-reapply`, `partially-detached`, `flat-reconstructed`, or `unavailable`.
- `reapplySummary`: user-facing effect summary for still-bound and detached steps.
- `warning`: optional warning used when binding reconstruction is incomplete.

Validation rules:

- A binding cannot be created by selecting a preset alone; it is created only after explicit apply or recoverable edit/rerun reconstruction.
- `flat-reconstructed` and `unavailable` bindings must display warning copy before the user relies on composition state.

## StepDraft.source

Represents the source relationship for one draft step.

Variants:

- `local`: manually authored step with no preset binding.
- `preset-bound`: step is still bound to an applied preset binding and source blueprint.
- `preset-detached`: step originated from a preset but manual edits detached it from source identity.
- `flat-reconstructed`: edit/rerun recovered the concrete step but not its binding state.

Core fields:

- `kind`: one of the source variants.
- `bindingId`: associated applied preset binding when present.
- `sourcePresetSlug`: preset that produced the step when known.
- `sourcePresetVersion`: pinned preset version when known.
- `sourceBlueprintSlug`: blueprint or step slug when known.
- `includePath`: include aliases from root to source when known.
- `provenance`: compact server-provided provenance for audit and grouped preview.
- `detachedReason`: `instructions-edited`, `attachments-edited`, `reordered`, `partial-selection`, or `unknown` when detached.

Validation rules:

- Manual instruction or attachment edits to a `preset-bound` step transition the source to `preset-detached`.
- `preset-detached` steps must preserve authored content and must not be overwritten by default reapply.
- Runtime submission uses flattened step content regardless of source variant.

## Grouped Composition Preview

Represents the presentation grouping for expanded preset steps.

Fields:

- `bindingId`: applied preset binding being previewed.
- `groups`: ordered groups derived from include path and provenance.
- `flatStepOrder`: concrete execution order for all steps in the draft.
- `errors`: non-mutating expansion or reconstruction warnings.

Validation rules:

- Grouping must not obscure the flattened execution order.
- Preview failures must not insert partial binding state into the draft.
