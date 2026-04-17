# Research: Enforce Image Artifact Storage and Policy

## Input Classification

Decision: Treat the MM-368 Jira preset brief as a single-story runtime feature request.
Rationale: The brief contains one operator story centered on image artifact storage and policy enforcement, with one acceptance set and one source design path.
Alternatives considered: Treating `docs/Tasks/ImageSystem.md` as a broad design would require `moonspec-breakdown`, but MM-368 already selected the storage/policy/security subset.

## Attachment Storage Surface

Decision: Reuse the existing Temporal artifact service and metadata tables for image bytes.
Rationale: The source design requires artifact-first storage and the repo already has `/api/artifacts` creation, upload, completion, metadata, and execution-link endpoints.
Alternatives considered: Adding a dedicated attachment table was rejected because the story requires no new durable storage and the artifact service already provides lifecycle and ownership.

## Server-Side Policy Enforcement

Decision: Enforce image policy both at artifact completion for Create-page attachment artifacts and at task-shaped execution submission for submitted `inputAttachments` refs.
Rationale: Completion catches invalid bytes as early as possible; execution submission catches disabled policy, incomplete refs, mismatched metadata, unsupported future fields, and clients that bypass browser checks.
Alternatives considered: Browser-only validation was rejected because MM-368 explicitly requires repeated server-side checks.

## Image Type Validation

Decision: Validate declared content type against the server allowlist and sniff compact magic bytes for PNG, JPEG, and WebP; always reject `image/svg+xml`.
Rationale: The source design names the default allowed image types and forbids scriptable image content. Magic-byte checks are deterministic and cheap.
Alternatives considered: Full image decoding was rejected as unnecessary for policy enforcement and would add dependency/CPU cost.

## Attachment Ref Shape

Decision: Accept only canonical ref fields: `artifactId`, `filename`, `contentType`, and `sizeBytes`.
Rationale: The story requires unsupported future fields to fail explicitly rather than being ignored.
Alternatives considered: Allowing unknown fields was rejected because it would silently drop unsupported attachment semantics.

## Execution Artifact Linkage

Decision: Preserve refs in the task parameters and original task input snapshot, and add submitted attachment IDs to execution artifact refs after execution creation.
Rationale: The task snapshot is the authoritative target-binding source, while execution artifact refs improve operator visibility without making metadata authoritative.
Alternatives considered: Relying only on client-side post-create link calls was rejected because non-browser API clients could otherwise start executions with unlinked attachment refs.

## Runtime Compatibility

Decision: Treat current task runtimes as compatible with artifact-backed attachment refs because prepare-time materialization and text context are the default desired state.
Rationale: No repo setting currently declares a runtime-specific incompatibility matrix. This story still fails explicitly for unsupported attachment fields and invalid policy states.
Alternatives considered: Blocking delegated runtimes such as Jules or Codex Cloud was rejected because no canonical repo requirement identifies them as incompatible for this story.

## Test Strategy

Decision: Add focused unit tests for validation helpers/service behavior and contract API tests for task-shaped execution submission.
Rationale: The highest-risk boundaries are artifact completion and execution request normalization.
Alternatives considered: Full compose-backed integration was deferred unless focused tests reveal service wiring gaps.
