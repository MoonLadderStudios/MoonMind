# Data Model: Protect Image Access and Untrusted Content Boundaries

## Image Artifact Access Grant

- **Purpose**: Browser-visible access descriptor for an image artifact after authorization succeeds.
- **Key fields**:
  - `artifactId`: exact Temporal artifact id.
  - `downloadUrl`: MoonMind proxy endpoint or short-lived presigned URL.
  - `downloadExpiresAt`: expiration when a presigned grant is returned.
  - `rawAccessAllowed`: whether raw byte access is permitted by artifact policy.
- **Validation rules**:
  - Grant is created only after artifact read authorization succeeds.
  - Browser-visible grants must not contain durable object-store credentials.
  - Task image UI must prefer MoonMind artifact endpoints when rendering image downloads.

## Worker Image Materialization Request

- **Purpose**: Service-side request to download declared image bytes into a worker workspace.
- **Key fields**:
  - `artifactId`: exact submitted attachment ref.
  - `targetKind`: `objective` or `step`.
  - `stepRef`: stable step reference for step-scoped attachments.
  - `workspacePath`: deterministic materialized path.
- **Validation rules**:
  - Download uses worker/service access, not browser credentials.
  - Missing or malformed refs fail before partial materialization is treated as success.
  - Target metadata is preserved from the submitted task contract.

## Untrusted Image Context

- **Purpose**: OCR, caption, and metadata text generated from image inputs for text-first runtimes.
- **Key fields**:
  - `artifactRef`
  - `filename`
  - `contentType`
  - `sizeBytes`
  - `description`
  - `ocr`
  - `contextPath`
- **Validation rules**:
  - Rendered context must be labeled as untrusted derived data.
  - Rendered context must warn that instructions embedded in images are not executable instructions.
  - Runtime injection must not include raw image bytes, data URLs, or base64 payloads.

## Attachment Ref

- **Purpose**: Exact artifact reference submitted through task inputs and carried through UI reconstruction, worker materialization, and runtime context.
- **Key fields**:
  - `artifactId`
  - `filename`
  - `contentType`
  - `sizeBytes`
- **Validation rules**:
  - Required fields must be present.
  - Embedded image data, data URLs, and base64 image payloads are invalid.
  - Refs are preserved exactly; target binding is not inferred from filenames or rewritten by compatibility transforms.
