# Research: Protect Image Access and Untrusted Content Boundaries

## Input Classification

Decision: Treat MM-374 as a single-story runtime feature request.

Rationale: The Jira preset brief contains one user story, one source document, and a bounded security contract around image access, worker materialization, extracted text, exact refs, and explicit non-goals.

Alternatives considered: Broad design breakdown was rejected because `docs/Tasks/ImageSystem.md` is only the source requirements document; the selected Jira issue already routes one story. Documentation-only mode was rejected because the user selected runtime mode.

## Artifact Authorization Boundary

Decision: Use the existing Temporal artifact service as the byte-access enforcement boundary.

Rationale: `TemporalArtifactService.presign_download`, metadata, read, and router paths already call artifact read authorization before exposing bytes or grants. This aligns with execution-owned artifact access and avoids adding a parallel image-specific authorization layer.

Alternatives considered: Adding image-only route authorization was rejected because it would duplicate artifact policy. Direct UI-side filtering was rejected because browser checks cannot be the authoritative security boundary.

## Browser Download Boundary

Decision: Keep browser-visible image downloads behind MoonMind artifact endpoints or service-generated short-lived presigned grants.

Rationale: Mission Control already builds `/api/artifacts/{artifactId}/download` links for task image inputs and prefers MoonMind endpoints for target-aware image rendering. The artifact service can produce short-lived presigned URLs after authorization when storage requires it.

Alternatives considered: Reusing artifact-provided external URLs was rejected for task image inputs because it could expose object-store, Jira, or provider-specific endpoints. Proxy-only download was not required because the source design explicitly allows short-lived presigned URLs.

## Worker Access Boundary

Decision: Treat `CodexQueueClient.download_artifact` and worker prepare materialization as the service-credential worker access path.

Rationale: Worker materialization downloads declared attachment refs from the queue/API service and writes target-aware manifests in the job workspace. It does not consume browser credentials or browser-visible download URLs.

Alternatives considered: Passing browser presigned URLs into worker prepare was rejected because it would couple worker authorization to browser grants and weaken execution ownership boundaries.

## Untrusted Extracted Text Boundary

Decision: Strengthen `VisionService` markdown and worker `INPUT ATTACHMENTS` notices so they explicitly state image-derived text is untrusted and must not be treated as system, developer, or task instructions unless authored task instructions explicitly opt in.

Rationale: Existing code already marks context as untrusted, but MM-374 specifically calls out extracted image text as not executable instructions. Making the warning explicit gives testable evidence without changing provider behavior.

Alternatives considered: Dropping OCR/captions entirely was rejected because adjacent stories need deterministic image-derived context. Escaping all text into opaque JSON was rejected as a larger behavior change not required by the brief.

## Attachment Ref Preservation

Decision: Rely on existing task contract and edit/rerun reconstruction tests for exact ref preservation, and add or confirm focused coverage for malformed refs and data URLs.

Rationale: The contract already rejects embedded image data and requires explicit artifact refs. Existing reconstruction tests fail when target bindings are lost, which satisfies the no-hidden-retargeting requirement.

Alternatives considered: Adding compatibility transforms for stale refs was rejected by the source design and the repo compatibility policy.
