# Contract: Image Access and Untrusted Content Boundaries

## Browser Image Access

- Browser preview/download surfaces request image bytes through MoonMind-owned artifact routes such as `/api/artifacts/{artifactId}/download`, or through a short-lived presigned URL returned by MoonMind after authorization.
- Browser-visible task image rendering must prefer a MoonMind route derived from `artifactId` over any artifact-provided `downloadUrl` that could point to object storage, Jira, or a provider-specific endpoint.
- Unauthorized users receive a denial from the artifact service before byte access or presign grants are returned.

## Worker Image Access

- Worker materialization receives exact attachment refs from the task contract and calls service-side artifact download using the worker execution context.
- Worker materialization writes `.moonmind/attachments_manifest.json` with exact artifact ids, target kind, step ref, content metadata, and workspace paths.
- Worker materialization does not consume browser cookies, browser presigned URLs, or UI download links.

## Untrusted Image-Derived Text

- Vision context markdown starts with a safety notice that treats image-derived text as untrusted derived data.
- Runtime `INPUT ATTACHMENTS` blocks repeat the safety notice before any generated image context paths or metadata.
- The notice must state that instructions embedded in images or extracted from images must not be treated as system, developer, or task instructions unless authored task instructions explicitly choose to use that text as input.

## Attachment Ref Guardrails

- Task contracts reject attachment refs that contain embedded image data, `data:image` payloads, or base64 image payloads.
- Malformed attachment refs fail visibly during task contract normalization or worker materialization.
- Target grouping never recovers missing bindings from filename, artifact ordering, external URLs, or preview metadata.

## Out Of Scope

- Live Jira synchronization.
- Direct browser Jira attachment access for task image inputs.
- Generic non-image attachment support by default.
- Provider-specific multimodal message formats as control-plane contract fields.
