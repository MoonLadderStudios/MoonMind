# Contract: Create Page Visible Step Attachments

## Scope

This contract describes the Create-page UI behavior for MM-410. It does not change the artifact API, execution API, or workflow payload schema.

## Policy Disabled

- No objective or step attachment entry points are visible.
- Text-only task authoring and submission remain available.
- Existing task, repository, runtime, preset, and Jira fields remain usable.

## Policy Enabled: Step Open Control

- Each rendered step has one compact + attachment button near the step Instructions field.
- The button accessible name is:
  - `Add images to Step N` when every allowed content type starts with `image/`
  - `Add attachments to Step N` when any allowed content type is non-image
- The button opens a file picker scoped to the owning step.
- The hidden file input preserves:
  - multiple selection
  - configured accept filtering
  - step identity through stable local step id

## Selection Semantics

- Selecting files appends to the owning step's existing local selections.
- Exact duplicates for the same target, identified by name, size, type, and lastModified, are deduped.
- Identical filenames on different steps remain separate target-owned selections.
- Removing or retrying a file affects only the owning target.
- Generated attachment markdown is never appended to task or step instructions.

## Rendering Semantics

- Selected and persisted attachments render under the owning step.
- Each attachment row exposes filename, content type, size, supported preview, error/upload state when relevant, and remove action.
- Preview failure keeps metadata and removal visible.
- Upload failure keeps retry and remove visible.

## Submission Semantics

- Local files upload to `/api/artifacts` before `/api/executions`.
- Submission is blocked while any attachment is invalid, failed, incomplete, or uploading.
- Step attachment refs are sent only through the owning `task.steps[n].inputAttachments`.
- Objective attachment refs are sent only through `task.inputAttachments`.
- Raw binary data, base64, data URLs, and generated attachment markdown are not sent in execution payloads.
