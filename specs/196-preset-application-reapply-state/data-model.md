# Data Model: Preset Application and Reapply State

## Preset Objective Input

- `templateFeatureRequest`: preset-owned objective text.
- `objectiveAttachmentFiles`: local files selected for the preset objective target.
- `objectiveAttachmentRefs`: uploaded artifact references submitted as task-level `inputAttachments`.
- Validation: governed by server-provided attachment policy; invalid attachments block submission before upload.

## Applied Preset State

- `appliedTemplates`: existing applied template metadata sent as `appliedStepTemplates`.
- `appliedTemplateFeatureRequest`: text value captured after the last successful Apply/Reapply.
- `appliedTemplateObjectiveAttachmentSignature`: normalized identity of objective-scoped attachments captured after the last successful Apply/Reapply.
- State transition: clean after successful Apply/Reapply; dirty when objective text or objective attachment signature differs from the last applied values.

## Template-Bound Step

- `id`: submitted template step ID only while the step remains template-bound.
- `templateStepId`: original template step ID.
- `templateInstructions`: original template-authored instructions.
- `templateAttachments`: original template-authored attachment identity snapshot.
- State transition: instruction edits clear `id`; attachment-set edits clear template input identity and prevent preserving stale template step identity in the submitted payload.

## Attachment Identity

- Artifact-backed attachment identity: artifact ID.
- Local or imported attachment identity: filename, content type, and size.
- Comparison: order-insensitive set comparison for detachment checks.
