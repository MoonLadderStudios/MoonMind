# Data Model: Edit and Rerun Attachment Reconstruction

## Authoritative Task Input Snapshot

Purpose: Durable task input state used to reconstruct edit and rerun drafts.

Fields:
- `task.instructions`: objective text.
- `task.inputAttachments`: objective-scoped structured attachment refs.
- `task.steps[n].id`: stable logical step identity when available.
- `task.steps[n].instructions`: step instruction text.
- `task.steps[n].inputAttachments`: step-scoped structured attachment refs.
- Runtime, publish, repository, template or preset, dirty-state, and dependency fields when present in the editable contract.

Validation rules:
- Snapshot reconstruction uses this structure as the source of truth.
- Attachment binding is valid only when the ref appears on the objective target or a concrete step target.
- Compact attachment metadata cannot replace structured task binding fields.

## Reconstructed Draft

Purpose: Browser-editable state derived from an existing MoonMind.Run.

Fields:
- Objective text.
- Objective persisted attachments.
- Step instructions.
- Step persisted attachments.
- Local files selected during edit or rerun but not yet uploaded.
- Runtime and publish settings.
- Template or preset binding state, including flat reconstruction warning state when binding metadata is unrecoverable.
- Editable dependencies.

Validation rules:
- Persisted refs and local files remain distinguishable.
- Untouched persisted refs survive edit and rerun submit.
- Add, remove, and replace actions mutate only the authored target.
- Missing structured attachment binding causes explicit reconstruction failure.

## Attachment Ref

Purpose: Structured reference to an uploaded input attachment.

Fields:
- `artifactId`
- `filename`
- `contentType`
- `sizeBytes`

Validation rules:
- `artifactId` is required for persisted refs.
- Filename and metadata are display attributes, not target-binding authority.
- Binary content is never embedded in reconstructed drafts or execution payloads.

## Attachment Target Binding

Purpose: Relationship between an attachment ref and its destination.

Targets:
- Objective target: `task.inputAttachments`
- Step target: `task.steps[n].inputAttachments`

State transitions:
- Persisted unchanged: ref remains on the same target.
- Removed: ref is omitted from that target by explicit user action.
- Added: uploaded local image becomes a structured ref on the authored target.
- Replaced: old persisted ref is removed and new uploaded ref is added on the authored target.
- Unreconstructable: draft reconstruction fails explicitly.
