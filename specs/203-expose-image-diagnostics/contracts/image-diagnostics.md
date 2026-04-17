# Contract: Image Input Diagnostics

## Diagnostic Event Classes

The runtime must emit or expose the following image-input diagnostic event classes when the corresponding lifecycle action occurs:

| Event | Status | Required target fields |
| --- | --- | --- |
| `attachment_upload_started` | `started` | `targetKind`, optional `stepRef` |
| `attachment_upload_completed` | `completed` | `targetKind`, optional `stepRef`, `artifactId` |
| `attachment_validation_failed` | `failed` | `targetKind`, optional `stepRef`, sanitized `error` |
| `prepare_download_started` | `started` | `targetKind`, optional `stepRef`, `artifactId` |
| `prepare_download_completed` | `completed` | `targetKind`, optional `stepRef`, `artifactId`, `workspacePath` |
| `prepare_download_failed` | `failed` | `targetKind`, optional `stepRef`, `artifactId`, sanitized `error` |
| `image_context_generation_started` | `started` | `targetKind`, optional `stepRef` |
| `image_context_generation_completed` | `completed` | `targetKind`, optional `stepRef`, `contextPath` |
| `image_context_generation_failed` | `failed` | `targetKind`, optional `stepRef`, sanitized `error` |
| `image_context_generation_disabled` | `disabled` | `targetKind`, optional `stepRef`, optional `contextPath` |

## Prepared Task Diagnostics

Prepared task diagnostics must expose:

- attachment count
- attachment manifest path when the manifest exists
- image context index path when the index exists
- generated context path and status for each objective or step target
- target-aware attachment metadata sufficient to distinguish objective-scoped and step-scoped image inputs

## Safety Rules

- Diagnostics must not expose raw image bytes.
- Diagnostics must not expose credentials, auth headers, cookies, provider tokens, or storage-provider secret material.
- Target binding must come from the task input snapshot, materialization target metadata, or manifest target metadata.
- Diagnostics must not infer target binding from filenames, artifact links, attachment ordering, UI heuristics, or raw workflow history.

## Completion Evidence

The story is complete when unit or boundary tests verify every in-scope MM-375 acceptance criterion and final MoonSpec verification confirms coverage of `DESIGN-REQ-019`.
