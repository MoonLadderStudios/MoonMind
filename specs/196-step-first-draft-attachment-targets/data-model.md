# Data Model: Step-First Draft and Attachment Targets

## AttachmentTarget

Represents the authored destination for an image input.

- `kind`: `objective` or `step`
- `stepLocalId`: required only when `kind = step`

Validation:
- Step targets must reference an existing draft step local ID.
- Objective targets are independent from the primary step.

## DraftAttachment

Represents a selected or uploaded image in the browser draft.

- `localId`: browser-local identity for UI operations
- `target`: `AttachmentTarget`
- `source`: `local` or `artifact`
- `status`: `selected`, `uploading`, `uploaded`, or `failed`
- `artifactId`: present after upload succeeds
- `filename`: display filename
- `contentType`: normalized content type
- `sizeBytes`: file size
- `previewUrl`: optional preview URL
- `errorMessage`: optional upload/validation error

Validation:
- Files must satisfy the server-provided attachment policy before upload.
- Failed attachments cannot be submitted as refs.
- Uploaded refs must include `artifactId`, `filename`, `contentType`, and `sizeBytes`.

## StepDraft

Represents one authored step.

- `localId`: stable browser identity used for attachment ownership
- `id`: optional persisted/template step identity
- `title`
- `instructions`
- `skillId`
- `skillArgs`
- `skillRequiredCapabilities`
- `templateStepId`
- `templateInstructions`
- `attachments`: step-scoped `DraftAttachment` records

Validation:
- Step 1 is Primary.
- Step 1 must contain instructions or an explicit skill for single-step submission.
- Step 1 must contain instructions when additional steps exist.
- Non-primary steps may omit instructions or skill.

## TaskDraft

Represents the Create page draft.

- `presetObjectiveText`
- `presetObjectiveAttachments`: objective-scoped `DraftAttachment` records
- `steps`: ordered `StepDraft` list
- `appliedTemplates`
- `runtime`, `providerProfile`, `model`, `effort`
- `repository`, `startingBranch`, `targetBranch`, `publishMode`
- `dependencies`

Submission mapping:
- `presetObjectiveAttachments` -> `task.inputAttachments`
- `steps[n].attachments` -> `task.steps[n].inputAttachments`
- Attachment refs are not appended to `task.instructions` or `task.steps[n].instructions`.

State transitions:
- `selected` -> `uploading` -> `uploaded`
- `selected` -> `uploading` -> `failed` -> `uploading` on retry
- Removing a target deletes only attachments owned by that target.
- Reordering steps changes authored order without changing attachment target ownership.
