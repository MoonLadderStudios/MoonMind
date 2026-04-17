# Research: Edit and Rerun Attachment Reconstruction

## Input Classification

Decision: Treat MM-382 as a single-story runtime feature request.

Rationale: The Jira preset brief contains one task author goal, one acceptance set, and one bounded behavior: reconstruct edit and rerun drafts from the authoritative task input snapshot while preserving attachment refs and explicit target bindings.

Alternatives considered: Broad design classification was rejected because the brief selects specific Create page sections and a single user story. Existing feature directory classification was rejected because no existing spec preserves MM-382 as the canonical input.

## Source Requirements

Decision: Treat `docs/UI/CreatePage.md` sections 13, 14, 16, and 18 as runtime source requirements.

Rationale: The user selected runtime mode and explicitly directed implementation documents to be treated as runtime source requirements. Those sections define edit/rerun reconstruction, artifact-first submission, failure behavior, and testing expectations.

Alternatives considered: Documentation-only alignment was rejected because docs mode was not requested.

## Implementation Surface

Decision: Validate the existing implementation surface in `frontend/src/lib/temporalTaskEditing.ts`, `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, `tests/unit/api/routers/test_executions.py`, and `tests/contract/test_temporal_execution_api.py`.

Rationale: Repository inspection found existing draft reconstruction, persisted attachment state, upload-before-submit, API snapshot, and contract tests matching MM-382 requirements. Reusing this behavior avoids duplicating implementation and keeps the MM-382 work focused on traceability and verification.

Alternatives considered: Creating a parallel reconstruction path was rejected because it would violate modularity and increase risk of divergent edit/rerun behavior.

## Attachment Binding Authority

Decision: The authoritative task input snapshot remains the only source for current attachment target binding during edit and rerun reconstruction.

Rationale: The source design and brief both prohibit inferring target binding from filenames, artifact links, or metadata. Structured fields under `task.inputAttachments` and `task.steps[n].inputAttachments` preserve the target relationship explicitly.

Alternatives considered: Inferring target from artifact metadata was rejected because it can be stale, incomplete, or ambiguous.

## Test Strategy

Decision: Use focused Vitest coverage for Create page reconstruction and submission behavior, pytest unit coverage for API action availability and snapshot descriptors, and pytest contract coverage for task snapshot attachment refs.

Rationale: MM-382 spans a browser reconstruction surface plus API/task snapshot contract evidence. Unit and contract tests cover the behavior without requiring external provider credentials.

Alternatives considered: Docker-backed integration only was rejected because it is slower and less targeted; provider verification is not relevant to this Create page/runtime contract.
