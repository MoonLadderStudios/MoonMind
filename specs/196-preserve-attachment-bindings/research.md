# Research: Preserve Attachment Bindings in Snapshots and Reruns

## Input Classification

Decision: Treat MM-369 as a single-story runtime feature request.
Rationale: The Jira brief contains one actor, one user goal, one acceptance set, and one bounded runtime behavior: reconstruct attachment bindings from the authoritative task input snapshot during edit and rerun.
Alternatives considered: Treating `docs/Tasks/ImageSystem.md` as a broad declarative design was rejected because the Jira brief selected only sections 5.3, 11, and 13 with specific coverage IDs.

## Authoritative Binding Source

Decision: Use the original task input snapshot artifact as the only authoritative source for edit/rerun attachment target binding.
Rationale: The source design explicitly forbids inferring target binding from filenames or artifact links, and the existing execution router already persists an original task input snapshot with `draft.task.inputAttachments`, `draft.task.steps[n].inputAttachments`, and a compact `attachmentRefs` index.
Alternatives considered: Reconstructing from artifact link metadata was rejected because metadata is observability only and can be stale or incomplete.

## Draft Reconstruction Boundary

Decision: Preserve persisted refs in the frontend Temporal task editing draft model, then map them into Create-page step state and submit payloads without re-uploading unchanged attachments.
Rationale: Edit and rerun are user-facing reconstruction flows in Mission Control; the existing `frontend/src/lib/temporalTaskEditing.ts` helper centralizes draft reconstruction and `frontend/src/entrypoints/task-create.tsx` centralizes Create-page state and submission.
Alternatives considered: Backend-only reconstruction was rejected because the browser must distinguish persisted refs from new local files before the user submits changes.

## Failure Behavior

Decision: Fail explicit reconstruction paths when required binding fields are unavailable instead of silently presenting a text-only draft.
Rationale: The source design calls silent attachment loss a contract violation. Existing task editing already disables actions when the original snapshot is missing; this story extends that behavior to attachment binding completeness.
Alternatives considered: Allowing degraded read-only drafts was rejected for active edit/rerun submission because it can lead to unintentional attachment loss.

## Test Strategy

Decision: Use Vitest unit tests for draft reconstruction and Create-page state behavior, pytest unit tests for backend snapshot descriptors and payload construction, and contract tests for artifact-backed task snapshots.
Rationale: The behavior crosses frontend state reconstruction, API serialization, and artifact snapshot shape. Focused unit tests catch transformation mistakes; contract tests verify persisted API boundary behavior.
Alternatives considered: Full compose-backed integration as the only proof was rejected because the required behavior is largely deterministic at API/UI contract boundaries and compose may be unavailable in managed-agent containers.
