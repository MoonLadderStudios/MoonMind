# Research: Policy-Gated Image Upload and Submit

## Input Classification

Decision: Treat the MM-380 Jira preset brief as a single-story runtime feature request.

Rationale: The brief has one actor, one Create page surface, one bounded outcome, and concrete acceptance criteria for policy-gated image attachment upload and submit behavior.

Alternatives considered: Treating `docs/UI/CreatePage.md` as a broad declarative design was rejected because the Jira issue already selects sections 11, 14, 16, and 18 for one story. Treating the work as docs-only was rejected because the selected mode is runtime.

## Source Requirements

Decision: Use `docs/UI/CreatePage.md` sections 11, 14, 16, and 18 as runtime source requirements.

Rationale: The Jira brief names those sections and coverage IDs. They define the desired attachment policy, submission, failure, empty-state, and testing behavior.

Alternatives considered: Reading every Create page section as in scope was rejected because it would broaden the story beyond MM-380. Ignoring the document and using only the brief was rejected because the brief explicitly points to that source design.

## Implementation Surface

Decision: Implement in the existing Create page entrypoint, `frontend/src/entrypoints/task-create.tsx`, with focused coverage in `frontend/src/entrypoints/task-create.test.tsx`.

Rationale: Current code already reads `dashboardConfig.system.attachmentPolicy`, tracks objective and step attachments, uploads local attachments to artifacts, and submits structured refs. The remaining story behavior is a UI and payload-contract completion around that existing surface.

Alternatives considered: Adding a new Create page component hierarchy was rejected as unnecessary scope. Adding backend storage or schema changes was rejected because existing artifact APIs and task payloads already support `inputAttachments`.

## Attachment Policy Behavior

Decision: Treat attachment policy as the authoritative UI gate for visibility, validation limits, allowed content types, and submit blocking.

Rationale: The source design requires attachment behavior to be policy-gated and runtime-configured. The Create page already receives runtime configuration in the boot payload.

Alternatives considered: Hardcoding image limits in the UI was rejected because runtime configurability is a constitution requirement. Relying only on backend rejection was rejected because the brief requires fail-fast browser validation.

## Upload And Submit Flow

Decision: Upload local images before create, edit, or rerun submission and submit only artifact-backed structured refs through `task.inputAttachments` and `task.steps[n].inputAttachments`.

Rationale: The source design requires artifact-first submission and explicit target fields. Existing backend contract tests already cover structured ref preservation, so the Create page should preserve that contract.

Alternatives considered: Embedding image bytes or generated markdown in task instructions was rejected because the source design forbids binary payload transport and filename-derived target binding.

## Test Strategy

Decision: Use Vitest for both unit-style and integration-style Create page coverage, then run `./tools/test_unit.sh` for final repository verification.

Rationale: The feature is UI-focused, and the existing Create page test harness can verify boot policy handling, file selection validation, artifact upload mocks, submit payloads, and failure messages without external services.

Alternatives considered: Docker-backed integration tests were rejected for this story because no compose-backed service boundary changes are planned. Python-only API tests were rejected as insufficient for browser-side visibility, validation, and upload-before-submit behavior.
