# Research: Preset Application and Reapply State

## Preset Dirty State

Decision: Track preset reapply state from both preset objective text and objective-scoped attachment changes after a preset has been applied.

Rationale: MM-378 requires changed preset objective inputs to mark the applied preset as dirty without automatically overwriting expanded steps. The existing Create page already has `presetReapplyNeeded`; expanding the state trigger is the smallest coherent change.

Alternatives considered: Re-expanding automatically on text change was rejected because the source design forbids silent overwrites. Storing dirty state only in submitted payload was rejected because the user needs visible feedback before submission.

## Objective-Scoped Attachments

Decision: Add a preset objective attachment target that uses the existing attachment policy, upload validation, artifact creation, and task-level `inputAttachments` payload shape.

Rationale: The source design requires objective images to belong to the preset-owned objective field and to submit through task-level attachments. Reusing existing upload helpers keeps behavior consistent with step attachments.

Alternatives considered: Reusing Step 1 attachments for preset objective images was rejected because it binds objective context to a step-specific target and obscures reapply dirty state. Embedding attachment references in instruction text was rejected by the artifact policy and source design.

## Template Attachment Detachment

Decision: Capture a template attachment snapshot on preset-expanded steps and compare authored attachment identity against that snapshot before preserving template input identity.

Rationale: Template-bound steps must detach when attachment sets no longer match the template input contract. A compact browser-side identity snapshot is sufficient for local draft comparisons and does not require backend storage changes.

Alternatives considered: Treating any step attachment change as instruction detachment only was rejected because the source design distinguishes instruction and input identity. Deferring detachment to the backend was rejected because the Create page has enough draft state to submit the correct identity.

## Test Strategy

Decision: Use focused Vitest tests in `frontend/src/entrypoints/task-create.test.tsx` for both unit-like state helpers and request-shape integration behavior.

Rationale: The story is a browser UI state and payload normalization change. Existing tests already mock MoonMind REST endpoints and inspect Create page requests, giving reliable coverage without compose services.

Alternatives considered: Adding backend tests was rejected because no backend contract changes are planned. Playwright was rejected because the existing Create page coverage is Vitest-based and faster for this state flow.
