# Research: Expose Image Diagnostics and Failure Evidence

## Runtime Diagnostic Boundary

Decision: Emit image-input diagnostic metadata at the existing prepare materialization and vision context generation boundaries.

Rationale: Those boundaries already receive authoritative objective and step attachment targets, workspace paths, manifest paths, context paths, and failure reasons. Emitting diagnostics there avoids raw workflow-history scraping and avoids reconstructing target binding from filenames or UI state.

Alternatives considered: Emitting diagnostics only from the UI was rejected because it would miss runtime prepare failures. Reconstructing diagnostics from workflow history was rejected because MM-375 explicitly forbids raw history heuristics.

## Evidence Path Discovery

Decision: Expose attachment manifest and generated context paths through the prepared task context diagnostics payload and per-target vision context index entries.

Rationale: The worker already writes `task_context.json`, `.moonmind/attachments_manifest.json`, and `.moonmind/vision/image_context_index.json`. Extending these compact payloads keeps evidence paths operator-owned and deterministic without new storage.

Alternatives considered: Adding a database table was rejected because the story needs deterministic runtime evidence and no new persistent storage. Logging paths only to text logs was rejected because logs are harder to query and parse.

## Event Shape

Decision: Use compact JSON-compatible diagnostic events with `event`, `targetKind`, optional `stepRef`, optional `artifactId`, optional `workspacePath`, optional `contextPath`, `status`, and optional sanitized error detail.

Rationale: This event shape is easy to validate in unit tests, keeps payloads small, and directly maps to DESIGN-REQ-019 lifecycle events.

Alternatives considered: Reusing provider-specific vision result payloads was rejected because diagnostics must stay runtime-neutral and must not expose provider credentials or raw image bytes.

## Test Strategy

Decision: Add focused unit tests for prepare materialization diagnostics and vision-context diagnostics, and rely on existing filesystem integration coverage for artifact output paths.

Rationale: The behavior is concentrated in deterministic Python helpers and worker prepare code. Unit tests can cover red-first event emission, path discovery, failure metadata, target binding, and disabled generation without external services.

Alternatives considered: Full compose integration for every diagnostic event was rejected for iteration speed; the hermetic integration runner remains available for broader validation.
