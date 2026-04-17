# Research: Generate Target-Aware Vision Context Artifacts

## Runtime Boundary

Decision: Extend `moonmind.vision.service.VisionService` with target-aware rendering and artifact-writing helpers.

Rationale: `VisionService` already resolves `VisionConfig`, checks provider availability, and renders deterministic Markdown for attachment metadata. Keeping target-aware artifact generation there preserves a single boundary for derived vision context.

Alternatives considered: Adding generation directly to worker prepare code was rejected because this story can be implemented as a reusable service capability first, with prepare integration able to call the service without duplicating rendering logic.

## Target Model

Decision: Represent each target as an explicit `VisionContextTargetInput` with `target_kind` of `objective` or `step`, optional `step_ref`, and a sequence of `AttachmentContextInput`.

Rationale: Target meaning must come from the explicit binding, not filenames or artifact links. A target model lets the service write deterministic paths and index entries without looking at unrelated targets.

Alternatives considered: Passing a flat list of attachments with encoded path prefixes was rejected because it could reintroduce filename/path inference and would make step target validation weaker.

## Artifact Paths

Decision: Use `.moonmind/vision/task/image_context.md` for the objective target, `.moonmind/vision/steps/<stepRef>/image_context.md` for step targets, and `.moonmind/vision/image_context_index.json` for the index.

Rationale: These paths are specified by `docs/Tasks/ImageSystem.md` section 9 and MM-371. Sanitizing `stepRef` prevents path traversal while preserving stable target identity for normal step IDs.

Alternatives considered: Writing one combined Markdown file was rejected because the source design requires target-scoped context artifacts and an index.

## Disabled And Provider-Unavailable Behavior

Decision: Disabled and provider-unavailable states still produce deterministic target status entries and Markdown for targets with attachments, while `VisionContext.enabled` remains false.

Rationale: MM-371 requires disabled generation not to block manifest/raw materialization and requires generated output to remain auditable. Existing `render_context()` already returns deterministic Markdown for disabled/provider-unavailable states, so target-aware generation can reuse that behavior.

Alternatives considered: Omitting context files when disabled was rejected because it would make the index less auditable and could look like target data was lost.

## Test Strategy

Decision: Use unit tests for rendering/index determinism and an integration-style filesystem test for output paths.

Rationale: The story is mostly deterministic transformation plus file writing. Unit tests can isolate statuses and traceability, while the filesystem test validates the observable runtime artifact paths.

Alternatives considered: Full Temporal workflow tests were deferred because no workflow/activity signature is changed in this story; adding the service capability is enough for this vertical slice.
