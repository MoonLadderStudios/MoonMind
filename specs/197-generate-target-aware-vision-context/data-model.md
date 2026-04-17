# Data Model: Generate Target-Aware Vision Context Artifacts

## VisionContextTargetInput

Represents one explicit context generation target.

- `target_kind`: Required target kind. Valid values: `objective` or `step`.
- `attachments`: Ordered source image attachment metadata for this target.
- `step_ref`: Required when `target_kind` is `step`; absent for `objective`.

Validation rules:

- Objective targets must not require or use a step reference for their output path.
- Step targets must provide a non-blank step reference.
- Step references must be normalized to a filesystem-safe path segment before writing artifacts.
- Target ordering must not change the path for another target.

## AttachmentContextInput

Existing metadata for one materialized source image.

- `id`: Source artifact reference or artifact ID.
- `filename`: Original uploaded filename.
- `content_type`: MIME type when known.
- `size_bytes`: Uploaded byte size.
- `digest`: Optional source digest.
- `local_path`: Materialized workspace path.
- `user_caption_hint`: Optional human hint.

Validation rules:

- Generated context must include `id` and `local_path` so derived data remains traceable to the source image ref and materialized file.
- Attachment metadata must not include embedded image bytes.

## VisionContextArtifact

Represents generated Markdown for one target.

- `target_kind`: `objective` or `step`.
- `step_ref`: Present for step targets.
- `path`: Workspace-relative output path.
- `status`: Render status such as `ok`, `disabled`, `provider_unavailable`, or `no_attachments`.
- `attachment_ids`: Source attachment refs represented in the Markdown.
- `markdown`: Rendered untrusted derived data.

Validation rules:

- Objective artifact path is `.moonmind/vision/task/image_context.md`.
- Step artifact path is `.moonmind/vision/steps/<stepRef>/image_context.md`.
- Markdown includes safety notice and source attachment metadata.

## VisionContextIndex

Workspace-local JSON summary written to `.moonmind/vision/image_context_index.json`.

- `version`: Index schema version.
- `generated`: Boolean indicating whether context was enabled and provider-ready for at least one target.
- `targets`: Ordered target summaries.
- `config`: Compact provider/model/OCR configuration used for deterministic rendering.

Validation rules:

- Every target with attachments must have one index entry.
- Every index entry must include target kind, status, context path, and source attachment refs.
- Index content must be deterministic for the same source target set and model configuration.
