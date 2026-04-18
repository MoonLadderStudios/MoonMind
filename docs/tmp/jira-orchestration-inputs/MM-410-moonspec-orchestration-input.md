# MM-410 MoonSpec Orchestration Input

## Source

- Jira issue: MM-410
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add visible step attachment + button on Create page
- Labels: attachments, create-page, images, ui
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-410 from MM project
Summary: Add visible step attachment + button on Create page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-410 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-410: Add visible step attachment + button on Create page

User Story
As a task author, I can click a visible + button on each Create-page step to attach an image or other policy-permitted file, see the selected attachment clearly under that step, and submit only after MoonMind has uploaded the file as a structured artifact-backed input for that exact step.

Context
MoonMind already has substantial attachment infrastructure, but the Create page still does not present a clear, polished UI affordance for adding attachments to a step. Current behavior is mostly implemented behind policy-gated file input controls. This work makes the feature fully usable and visually correct as a first-class step authoring interaction.

Important Existing Surfaces
- Canonical design: `docs/UI/CreatePage.md`, especially sections 7.2, 7.3, 7.4, 11, 14, 16, and 18.
- Create page entrypoint: `frontend/src/entrypoints/task-create.tsx`.
- Create page tests: `frontend/src/entrypoints/task-create.test.tsx`.
- Create page styles: `frontend/src/styles/mission-control.css`.
- Runtime policy config: `api_service/api/routers/task_dashboard_view_model.py` and `moonmind/config/settings.py`.
- Artifact API: `api_service/api/routers/temporal_artifacts.py`.
- Execution attachment validation: `api_service/api/routers/executions.py`.
- Worker materialization boundary: `moonmind/agents/codex_worker/worker.py`.

Current State Discovered
- The Create page already reads `system.attachmentPolicy` and hides attachment controls when disabled.
- `AGENT_JOB_ATTACHMENT_ENABLED` currently defaults to false, so attachment entry points are hidden unless explicitly enabled.
- The Create page already keeps selected objective and step attachment state, renders selected files, validates type, count, size, and total size, previews images, supports remove and retry, uploads through `/api/artifacts` before submission, and submits structured refs under `task.inputAttachments` and `task.steps[n].inputAttachments`.
- The existing step UI is a normal file input block under each step, not the requested compact + button affordance.
- The current selection behavior replaces the target file list. A + button should add or append while preserving remove-per-file behavior.

Requirements

1. Policy-gated visibility
- Keep all attachment entry points hidden when `system.attachmentPolicy.enabled` is false.
- Do not block text-only manual authoring when attachments are disabled.
- Continue reading `allowedContentTypes`, `maxCount`, `maxBytes`, and `totalBytes` from server-provided runtime config.
- Decide whether this story changes the default for `AGENT_JOB_ATTACHMENT_ENABLED`. If not, document that operators must enable it for the UI to appear.

2. Per-step + button affordance
- Add a visible + button to each Create-page step region near the step Instructions field.
- The button must open a file picker for that specific step target.
- The button must be keyboard accessible and have a clear accessible name, for example `Add images to Step 1` or `Add attachments to Step 1` depending on policy.
- Preserve accept filtering from `attachmentPolicy.allowedContentTypes`.
- Support multiple file selection.
- The control must not look like a generic browser file input.

3. Target-specific attachment ownership
- Files added through a step + button must belong only to that step `localId`.
- Reordering steps must move the attachments with the owning step.
- Removing or retrying an attachment must affect only the owning target.
- The same filename on different steps must not cause target confusion.
- Do not append generated attachment markdown into task or step instruction text.

4. Append semantics
- Selecting files through + must append to existing selected files for that target instead of replacing the target file list.
- Dedupe exact duplicate selected files using stable local file identity such as name, size, type, and `lastModified`.
- Keep existing per-file removal behavior.
- Re-run validation after every add/remove action.

5. Attachment type support
- Use image-specific copy such as Images when every allowed content type starts with `image/`.
- Use generic copy such as Input Attachments or Attachments when the policy permits non-image content types.
- Image files should show thumbnails when preview is supported.
- Non-image files should show filename, content type, and size without a broken preview.
- Preview failure must keep metadata and remove action visible.

6. Visual design
- The + button should fit the existing Mission Control visual system and be stable in size.
- Prefer reusing or extending existing compact icon-button styling instead of adding a divergent control style.
- The button should sit close enough to the Instructions field that the target relationship is obvious.
- Selected attachments should render as compact rows or chips under the owning step, showing filename, type, size, preview when applicable, upload/error state when relevant, Retry, and Remove.
- The layout must be stable on mobile and desktop and must not shift dramatically when files are added, preview fails, or errors appear.
- The main Create-page flow must remain task-first and should not become visually dominated by the attachment UI.

7. Upload and submit behavior
- Continue uploading selected local files to the MoonMind artifact API before create, edit, or rerun submission.
- Continue submitting objective refs only through `task.inputAttachments`.
- Continue submitting step refs only through the owning `task.steps[n].inputAttachments`.
- Block submit while any attachment is invalid, failed, incomplete, or uploading.
- Keep artifact create metadata target-aware. Step uploads should include the `task-dashboard-step-attachment` source and a useful step label.
- Raw binary data, base64, data URLs, or embedded image data must never be sent in the execution payload.

8. Existing edit/rerun compatibility
- Persisted artifact-backed attachments reconstructed for edit/rerun must still display under their owning target.
- Removing persisted refs during edit must serialize explicit empty attachment lists where needed so removals are preserved.
- Newly selected files may coexist with persisted refs and must count against policy limits.

Acceptance Criteria
- Given attachment policy is disabled, no + attachment buttons are visible on objective or step targets, and a text-only task can still be submitted.
- Given image-only policy is enabled, each step shows a compact + button with image-oriented accessible copy and file accept filtering for the configured image MIME types.
- Given mixed allowed content types are enabled, the UI uses generic attachment copy and supports non-image files without attempting broken image preview.
- Given I click the + button on Step 1 and select a file, the file appears only under Step 1 with filename, type, size, preview when supported, and Remove action.
- Given I click + again on the same step and select another file, the new file is appended rather than replacing the previous file.
- Given I remove one selected file, unrelated files, steps, instructions, preset state, repository fields, runtime fields, and Jira state remain unchanged.
- Given a selected file violates allowed MIME type, per-file bytes, total bytes, or count policy, the error is visible at the affected target and upload is not attempted.
- Given upload fails for one step attachment, the failure remains scoped to that step, Retry and Remove are available, and task creation is blocked until resolved.
- Given preview fails, attachment metadata and removal remain available and draft state is not corrupted.
- Given submit succeeds with step attachments, `/api/artifacts` is called before `/api/executions` and the execution payload includes structured refs under the owning `task.steps[n].inputAttachments` only.
- Given submit succeeds with objective attachments, the execution payload includes structured refs under `task.inputAttachments` only.
- Given edit/rerun includes persisted attachments and newly selected local files, policy validation counts both and submission preserves target ownership.
- Given steps are reordered, selected attachments remain associated with the same logical step `localId`.

Implementation Notes
- Start in `frontend/src/entrypoints/task-create.tsx` around the existing attachment state and step rendering.
- Replace or hide the visible `input[type=file]` with a compact label/ref-backed + control.
- Preserve hidden file input accessibility and form semantics.
- Update `updateStepAttachments` or add a separate `appendStepAttachments` helper so + adds files without replacing existing selection.
- Keep `attachmentTargetKey(localId)` as the error and preview scope boundary.
- Keep `attachmentPolicy.enabled` as the feature gate.
- If changing `AGENT_JOB_ATTACHMENT_ENABLED` default, update settings tests and operator docs because this changes runtime visibility.
- Avoid changing artifact API or execution payload contracts unless a test proves an existing contract gap.

Testing Requirements
- Update `frontend/src/entrypoints/task-create.test.tsx` for + button visibility, accessibility, append semantics, image-only copy, mixed-content copy, disabled-policy hiding, scoped validation, upload failure, preview failure, and structured payload submission.
- Preserve existing tests for upload-before-submit and no instruction rewriting.
- Add or update backend tests only if policy defaults or execution validation behavior changes.
- Run focused UI tests with `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` or `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`.
- Before completion, run `./tools/test_unit.sh` for final unit verification.

Out of Scope
- New persistent storage tables.
- Browser-direct upload to Jira, GitHub, object storage, or model providers.
- Drag-and-drop or paste support unless it can be added without expanding scope; if added, gestures must still land on an explicit target step.
- Embedding binary, base64, or data URL content in task execution payloads.
- Changing worker attachment materialization semantics beyond what is needed to preserve the existing contract.

Dependencies
- Jira link metadata at fetch time indicates no issue links for MM-410.
