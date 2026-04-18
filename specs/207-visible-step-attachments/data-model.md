# Data Model: Visible Step Attachments

## StepAttachmentButton

- **Purpose**: User-facing control that opens a file picker for one step target.
- **Fields**:
  - `stepLocalId`: stable owning step identity
  - `stepNumber`: rendered step ordinal for user-facing labels
  - `labelKind`: `images` when all allowed content types are images, otherwise `attachments`
  - `accept`: comma-separated allowed content types from runtime policy
- **Validation**:
  - Rendered only when attachment policy is enabled.
  - Accessible name identifies the owning step and content kind.
  - Open action targets only the hidden input for the owning step.

## DraftAttachmentFile

- **Purpose**: Local file selected before artifact upload.
- **Fields**:
  - `file`: browser File object
  - `targetKey`: objective or stable step target
  - `identity`: `name`, `size`, `type`, and `lastModified`
  - `previewState`: not requested, loaded, or failed
  - `validationState`: valid or target-specific error
  - `uploadState`: pending, uploading, uploaded, or failed
- **Validation**:
  - Must satisfy count, per-file size, total size, and content type policy before upload.
  - Exact duplicate identity for the same target is deduped during append.
  - Same filename on different targets does not imply shared ownership.

## PersistedAttachmentRef

- **Purpose**: Artifact-backed attachment reconstructed during edit or rerun.
- **Fields**:
  - `artifactId`
  - `filename`
  - `contentType`
  - `sizeBytes`
  - owning objective or step target
- **Validation**:
  - Counts with newly selected files for policy limits.
  - Removal is serialized explicitly for changed targets.
  - Must remain under the owning target through edit/rerun submission.

## State Transitions

1. Policy disabled -> no attachment entry points render.
2. Policy enabled -> each step renders a compact + button with hidden file input.
3. User selects files -> files append to the owning step target after exact duplicate dedupe.
4. Validation runs -> target-specific errors render and invalid files do not upload.
5. Preview fails -> metadata remains visible and the file remains removable.
6. Upload fails -> retry/remove remains scoped to the owning target and submit is blocked.
7. Submit succeeds -> local files upload first, then structured refs are sent under owning step `inputAttachments`.
