# Create Page Story Breakdown

Source design: `docs/UI/CreatePage.md`
Original source document reference path: `docs/UI/CreatePage.md`
Requested output mode: `jira`
Story extraction date: 2026-04-17T01:29:57Z

Coverage gate result:

```text
PASS - every major design point is owned by at least one story.
```

## Design Summary

docs/UI/CreatePage.md defines the desired-state contract for a single MoonMind-native task authoring page. The design keeps the page task-first, step-first, target-aware for structured image inputs, API-boundary safe, and explicit about presets, Jira import, dependencies, execution options, edit/rerun reconstruction, artifact-first submission, failures, accessibility, and test coverage. The breakdown below groups implementation into independently testable vertical stories that preserve manual authoring while adding target-aware attachments, presets, Jira imports, execution controls, and durable edit/rerun behavior.

## Coverage Points

| ID | Type | Source Section | Design Point |
| --- | --- | --- | --- |
| DESIGN-REQ-001 | requirement | 1. Purpose; 3. Product stance; 19. Summary | The Create page is the single MoonMind-native task-authoring surface for manual steps, presets, Jira imports, dependencies, execution options, schedules, create, edit, and rerun; it is not a generic workflow builder, image editor, Jira-native surface, or binary transport layer. |
| DESIGN-REQ-002 | integration | 4. Route and hosting model | The canonical route is /tasks/new, hosted by FastAPI and rendered by the Mission Control React/Vite UI, with runtime config from the server boot payload and all actions through MoonMind APIs. |
| DESIGN-REQ-003 | security | 3. Product stance; 4. Route and hosting model; 12.1 Product role | Browser clients never call Jira, object storage, or model providers directly; artifact upload, preview, download, Jira access, and task submission stay behind MoonMind-controlled APIs. |
| DESIGN-REQ-004 | requirement | 5. Canonical page model | The page is one composition form ordered as Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, and Submit. |
| DESIGN-REQ-005 | state-model | 6. Draft model | The browser draft is step-first and target-aware, with structured DraftAttachment state for objective or step targets, upload state, artifact refs, previews, persisted vs local distinctions, runtime fields, dependencies, and applied templates. |
| DESIGN-REQ-006 | requirement | 3. Product stance; 5. Canonical page model; 6. Draft model; 14. Submission contract; 19. Summary | Images are structured inputs attached to explicit objective or step targets, never pasted binary content, instruction text, filename conventions, anonymous buckets, or implicit target copies. |
| DESIGN-REQ-007 | requirement | 7.1 Step list; 7.2 Step fields | The step editor renders step cards, keeps Step 1 Primary, supports add/remove/reorder, remains valid with one step, moves attachments with reordered steps, and enforces primary/non-primary instruction and skill rules. |
| DESIGN-REQ-008 | requirement | 7.3 Step attachment contract | Step attachments submit through task.steps[n].inputAttachments, show metadata and preview/error state, can be removed before submit, and stay bound to the explicit step through reorder, edits, presets, drag-and-drop, or paste. |
| DESIGN-REQ-009 | requirement | 7.4 Objective-scoped attachment target; 15. Objective resolution and title derivation | Objective-scoped attachments submit through task.inputAttachments, belong to the preset objective target, may be hidden if presets are disabled, participate in task-level objective context, and are not copied into step attachments. |
| DESIGN-REQ-010 | state-model | 7.5 Template-bound steps | Preset-expanded steps remain template-bound only while authored instructions and attachments match the template input contract; instruction edits, attachment edits, Jira text imports, and Jira image imports detach identity. |
| DESIGN-REQ-011 | requirement | 8. Task preset contract | The optional preset area exposes preset selection, objective text, objective images, Apply, optional Save Current Steps as Preset, and status; selecting a preset alone does not mutate the draft and application is explicit. |
| DESIGN-REQ-012 | state-model | 8.3 Preset objective contract; 8.4 Reapply behavior | Preset objective text is preferred for objective resolution, objective attachments match that field, changes do not silently rewrite expanded steps, and changed objective text or attachments mark the applied preset as needing explicit reapply. |
| DESIGN-REQ-013 | requirement | 9. Dependency contract | The dependency area picks up to 10 existing MoonMind.Run executions, rejects duplicates client-side, does not block manual creation on fetch failure, and is independent from attachments, Jira, and presets. |
| DESIGN-REQ-014 | requirement | 10. Execution context contract | The page preserves runtime, provider profile, model, effort, repository, branch, publish mode, and merge automation controls, with server-provided defaults, runtime-specific profiles, resolver skill restrictions, and PR-only merge automation payload preservation. |
| DESIGN-REQ-015 | security | 10. Execution context contract | Jira import and image upload must never bypass or weaken repository validation, publish validation, or runtime gating; repository validation remains unchanged by Jira integration. |
| DESIGN-REQ-016 | requirement | 11. Attachment policy and UX contract | Attachment entry points are hidden when disabled, labels follow allowed MIME types, validation runs before upload and at submit, and count, size, content type, upload, invalid, and preview failures are visible and target-local. |
| DESIGN-REQ-017 | integration | 12. Jira integration contract | Jira sources task inputs into the Create page through supported targets for preset objective text, preset objective attachments, step instructions, and step attachments; it does not create tasks automatically, replace the editor or presets, change task API shape, or make the browser call Jira. |
| DESIGN-REQ-018 | requirement | 12.2 Supported targets; 12.3 Text and image import semantics; 12.4 Preset interaction | Selecting a Jira issue never mutates the draft automatically; text import supports replace/append, image import supports supported image selection, imported images become structured target attachments, target switching preserves issue selection, and preset imports dirty reapply state appropriately. |
| DESIGN-REQ-019 | state-model | 13. Edit and rerun contract | Edit and rerun reconstruct from the authoritative task input snapshot, including objective text, objective and step attachments, runtime and publish settings, templates and dirty state, dependencies, and persisted/local attachment distinctions. |
| DESIGN-REQ-020 | durability | 13. Edit and rerun contract; 16. Failure and empty-state rules | Users can keep, remove, add, or replace persisted attachments; rerun preserves untouched refs by default; edit/rerun fails explicitly if attachments cannot be reconstructed; attachments must never be silently dropped or duplicated. |
| DESIGN-REQ-021 | integration | 14. Submission contract | Local images upload to the artifact system before create, edit, or rerun submission; the browser submits structured attachment refs, the control plane stores an authoritative task input snapshot, upload completion is required before eligibility, and submit remains explicit. |
| DESIGN-REQ-022 | state-model | 15. Objective resolution and title derivation | Resolved objective text comes from preset objective text, then primary step instructions, then most recent applied preset request input; Jira target choice affects this ordering; title derivation uses the first non-empty line of resolved objective text. |
| DESIGN-REQ-023 | requirement | 16. Failure and empty-state rules | Disabled attachments hide entry points while preserving manual authoring; upload and preview failures are target-local; Jira unavailability and issue fetch failure do not mutate or erase manual draft state; invalid or incomplete attachments block submit explicitly. |
| DESIGN-REQ-024 | requirement | 17. Accessibility and interaction rules | Open, close, target, upload, remove, retry, and import actions are keyboard-accessible; image controls expose labels; step cards indicate attachments; Jira browser title identifies target; focus returns predictably after imports. |
| DESIGN-REQ-025 | verification | 18. Testing requirements | The test suite must cover policy-disabled attachments, validation limits, target isolation, reorder preservation, explicit Jira imports, template detachment, preset dirty state, artifact-first submission, edit/rerun reconstruction, failure isolation, and invalid/incomplete submit blocking. |

## Ordered Story Candidates

### Story 1: Canonical Create Page Shell

Story ID: `STORY-001`
Short name: `create-page-shell`
Source reference: `docs/UI/CreatePage.md`
Source sections: 1. Purpose, 3. Product stance, 4. Route and hosting model, 5. Canonical page model, 19. Summary

Why: As a task author, I can open the canonical Create page and use one MoonMind-native task composition form whose route, hosting, section order, and API boundaries are consistent across create, edit, and rerun entry points.

Scope:
- Expose /tasks/new as the canonical Create page route.
- Render task creation, edit, and rerun modes through the same task-first composition surface.
- Build runtime configuration server-side and pass it through the boot payload.
- Keep artifact, Jira, provider, and object-storage interactions behind MoonMind API surfaces.
- Preserve the canonical section order and task-first product stance.

Out of scope:
- Implementing attachment upload internals.
- Implementing Jira import semantics.
- Implementing edit/rerun reconstruction internals.

Independent test:
- Render the Create page at /tasks/new with a server boot payload and assert the expected single-form section order, page mode header, MoonMind API usage, and redirect behavior for any compatibility alias.

Acceptance criteria:
- Given I navigate to /tasks/new, then the server-hosted Mission Control UI renders the Create page from the server-provided runtime boot payload.
- Given a compatibility route exists, when I visit it, then it redirects to /tasks/new and does not define separate product behavior.
- Given the page renders, then the form sections appear in this order: Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
- Given any page action occurs, then the browser calls MoonMind REST APIs rather than Jira, object storage, or model providers directly.
- Given optional presets, Jira, or image upload are unavailable, then manual task authoring remains available.

Dependencies: None.

Risks or open questions:
- Existing aliases may need removal or redirect-only enforcement if they currently carry page behavior.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004

### Story 2: Step-First Draft and Attachment Targets

Story ID: `STORY-002`
Short name: `step-attachment-targets`
Source reference: `docs/UI/CreatePage.md`
Source sections: 6. Draft model, 7.1 Step list, 7.2 Step fields, 7.3 Step attachment contract, 7.4 Objective-scoped attachment target

Why: As a task author, I can build a step-first draft where instructions, skills, and image inputs belong to explicit objective or step targets and stay attached to the correct target through normal editing.

Scope:
- Represent attachments as structured DraftAttachment records with objective or step targets.
- Distinguish selected, uploading, uploaded, failed, local-file, and artifact-backed attachment states.
- Keep attachments out of instruction text and detached from filename or ordering conventions.
- Render step attachments in the same card as the step instructions they inform.
- Support add, remove, and reorder for steps without creating dependency edges.
- Allow objective-scoped attachments only as task-level objective inputs, not automatic step copies.
- Cover target isolation and reorder preservation in tests.

Out of scope:
- Preset expansion behavior beyond objective attachment target availability.
- Jira image selection and import.
- Artifact upload implementation.

Independent test:
- Create a draft with objective and step attachments, add/remove/reorder steps, and assert structured target ownership, validation, keyboard-accessible controls, and payload mapping remain stable.

Acceptance criteria:
- Given the draft contains one step, then Step 1 is identified as Primary and the page remains valid when primary instructions or an explicit skill is present.
- Given additional steps exist, then the primary step requires instructions while non-primary steps may omit instructions or inherit the primary skill default.
- Given I add an image to a step, then it appears in that step card and submits through task.steps[n].inputAttachments.
- Given I add an objective-scoped image, then it belongs to the preset objective target and submits through task.inputAttachments.
- Given I reorder steps, then step attachments move with their owning steps and do not attach to another step implicitly.
- Given an attachment control is available, then open, upload, remove, retry, and target actions are keyboard accessible and labeled for assistive technology.

Dependencies: STORY-001.

Risks or open questions:
- Existing step IDs and local IDs must be reconciled carefully so reorder operations do not sever persisted attachments.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-024, DESIGN-REQ-025

### Story 3: Preset Application and Reapply State

Story ID: `STORY-003`
Short name: `preset-reapply-state`
Source reference: `docs/UI/CreatePage.md`
Source sections: 7.5 Template-bound steps, 8. Task preset contract, 15. Objective resolution and title derivation

Why: As a task author, I can apply reusable task presets explicitly, edit preset objective inputs, and understand when expanded steps need an explicit reapply without losing my manual step customizations.

Scope:
- Expose optional Preset, Feature Request / Initial Instructions, objective images, Apply, optional save-as-preset, and status controls.
- Treat applied preset steps as expanded blueprints rather than live bindings.
- Track template step identity only while authored instructions and attachments match the template input contract.
- Store templateAttachments for detachment comparisons.
- Mark applied preset state dirty when preset objective text or objective-scoped attachments change.
- Resolve objective text from preset objective, then primary instructions, then the most recent applied preset request alias.
- Cover preset dirty state and template detachment in tests.

Out of scope:
- Jira browser implementation.
- Saving preset definitions beyond the optional control contract.
- Changing Jira Orchestrate parent-owned PR publishing behavior.

Independent test:
- Apply a preset to an empty draft and a non-empty draft, change objective text and attachments after apply, edit template-bound steps, and assert expanded steps, dirty reapply state, detachment state, and objective resolution are correct.

Acceptance criteria:
- Given only the initial empty step exists, when I apply a preset, then the preset may replace the placeholder step set with expanded blueprint steps.
- Given authored steps already exist, when I apply a preset, then expanded preset steps append to the current draft.
- Given I select a preset without pressing Apply, then the draft does not mutate.
- Given preset objective text is non-empty, then it is preferred over primary-step instructions for resolved objective text and title derivation.
- Given I change preset objective text or objective-scoped attachments after applying a preset, then the preset is marked as needing explicit reapply and expanded steps are not overwritten automatically.
- Given I manually edit a template-bound step instruction or attachment set, then that step detaches from template instruction or input identity.

Dependencies: STORY-002.

Risks or open questions:
- Preset reapply logic can accidentally overwrite manual edits unless template-bound state and dirty state are tested together.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-022, DESIGN-REQ-025

### Story 4: Dependencies and Execution Options

Story ID: `STORY-004`
Short name: `execution-options`
Source reference: `docs/UI/CreatePage.md`
Source sections: 9. Dependency contract, 10. Execution context contract, 5. Canonical page model

Why: As a task author, I can select run dependencies and configure runtime, repository, publish, merge automation, priority, attempts, proposals, and schedule options without those controls being weakened by Jira or images.

Scope:
- Provide a bounded dependency picker for existing MoonMind.Run executions.
- Preserve runtime, provider profile, model, effort, repo, branch, publish mode, priority, max attempts, propose tasks, schedule, and submit controls.
- Use server-provided runtime defaults and runtime-specific profile options.
- Respect resolver-style skill restrictions that force publish mode to none.
- Gate merge automation to ordinary PR-publishing tasks and copy that explains PR readiness gate plus pr-resolver behavior.
- Reject any Jira or image path that bypasses repository, publish, or runtime validation.

Out of scope:
- Implementing pr-resolver itself.
- Changing Jira Orchestrate preset publishing ownership.
- Attachment upload internals.

Independent test:
- Configure dependencies and execution context combinations, including resolver skills and merge automation, then assert client validation, rendered controls, and submitted payload fields match the contract.

Acceptance criteria:
- Given dependency search fails, then I can continue manual task creation without losing draft state.
- Given I add dependencies, then no more than 10 direct MoonMind.Run dependencies are accepted and duplicates are rejected client-side.
- Given runtime configuration is loaded, then runtime defaults and provider-profile options come from server-provided config and remain runtime-specific.
- Given publish mode is pr for an ordinary task, then merge automation can be selected and submission preserves publishMode=pr, task.publish.mode=pr, and mergeAutomation.enabled=true.
- Given publish mode is branch or none, or the task is a direct pr-resolver or batch-pr-resolver task, then merge automation is hidden or disabled and is not submitted.
- Given Jira import or image upload occurs, then repository validation, publish validation, and runtime gating are unchanged and still enforced.

Dependencies: STORY-001.

Risks or open questions:
- Merge automation copy and payload fields are easy to drift apart without explicit UI and request-body tests.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-004

### Story 5: Policy-Gated Image Upload and Submit

Story ID: `STORY-005`
Short name: `image-upload-submit`
Source reference: `docs/UI/CreatePage.md`
Source sections: 11. Attachment policy and UX contract, 14. Submission contract, 16. Failure and empty-state rules, 18. Testing requirements

Why: As a task author, I can add permitted image inputs, see validation and upload failures at the correct target, and submit only after local images become artifact-backed structured attachment refs.

Scope:
- Read attachmentPolicy from server-provided runtime configuration.
- Validate attachment count, per-file bytes, total bytes, and content type before upload and at submit time.
- Represent upload and preview failure states without silently dropping selected images.
- Provide keyboard-accessible remove and retry actions plus concise per-target summaries.
- Upload local images to the artifact system before create, edit, or rerun submission.
- Submit task.inputAttachments and task.steps[n].inputAttachments as structured artifact refs.
- Block submit while attachments are invalid, failed, incomplete, or still uploading.
- Cover policy, validation, failure isolation, upload-before-create, and invalid/incomplete submit blocking in tests.

Out of scope:
- Jira image import selection.
- Edit/rerun reconstruction of already persisted attachments beyond preserving target refs in submissions.

Independent test:
- Exercise attachment policy disabled/enabled states, invalid count/size/type inputs, failed previews, failed uploads, retry/remove actions, and final create submission payloads with artifact-backed refs.

Acceptance criteria:
- Given attachment policy is disabled, then all attachment entry points are hidden and the page remains fully usable for manual authoring.
- Given policy allows only image MIME types, then the UI uses an image-specific label such as Images.
- Given count, single-file size, total size, or content type validation fails, then the browser fails fast and visibly at the affected target before upload and again blocks submit if unresolved.
- Given upload fails, then the failure remains local to the affected target and I can remove or retry without losing unrelated draft state.
- Given preview fails, then attachment metadata remains visible, the draft is not corrupted, and removal remains available.
- Given I submit create, edit, or rerun with local images, then images upload to the artifact system first and the execution payload contains structured refs rather than binary content.

Dependencies: STORY-002.

Risks or open questions:
- Submit-time validation must re-check stale browser state so partially uploaded or failed attachments cannot leak into execution creation.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-016, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-006

### Story 6: Jira Import Into Declared Targets

Story ID: `STORY-006`
Short name: `jira-import-targets`
Source reference: `docs/UI/CreatePage.md`
Source sections: 12. Jira integration contract, 16. Failure and empty-state rules, 17. Accessibility and interaction rules, 18. Testing requirements

Why: As a task author, I can browse Jira as an external instruction source and explicitly import issue text or supported images into a declared Create page target without automatic draft mutation.

Scope:
- Support Jira import targets for preset objective text, preset objective attachments, step text, and step attachments.
- Require explicit confirmation for all Jira text and image imports.
- Preserve selected issue state while switching targets inside the browser.
- Import Jira images only as structured attachments on the declared target.
- Mark already-applied preset state as needing reapply when importing into preset objective text or attachment targets.
- Detach template-bound steps when Jira text or images import into them.
- Keep Jira access behind MoonMind APIs and separate from task execution substrate behavior.
- Cover explicit import, no-mutation-before-confirm, image target mapping, template detachment, focus return, and failure behavior in tests.

Out of scope:
- Creating MoonMind tasks automatically on issue selection.
- Replacing presets or the step editor with Jira-native workflow behavior.
- Browser-direct Jira API calls.

Independent test:
- Open the Jira browser from each supported target, select an issue, switch targets, import text and images with confirmation, simulate Jira fetch failures, and assert draft mutation, focus return, template detachment, and preset dirty state.

Acceptance criteria:
- Given I open Jira from a Create page field, then the browser preselects that matching target and displays the current target explicitly.
- Given I select a Jira issue, then the draft does not mutate until I confirm a text or image import action.
- Given I switch import targets inside the Jira browser, then the selected issue remains selected.
- Given I import text, then I can choose Replace target text or Append to target text for preset objective text or a step instruction target.
- Given I import supported Jira images, then selected images become structured attachments on the selected objective or step attachment target and are not injected as markdown, HTML, or inline data.
- Given Jira is unavailable or the issue fetch fails, then the draft is not mutated and I can continue manual authoring.
- Given import succeeds, then focus returns predictably to the updated field or an explicit success notice.

Dependencies: STORY-002, STORY-003, STORY-005.

Risks or open questions:
- Jira images and local uploads can diverge unless both paths share target-aware attachment validation and structured ref handling.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025

### Story 7: Edit and Rerun Attachment Reconstruction

Story ID: `STORY-007`
Short name: `edit-rerun-reconstruction`
Source reference: `docs/UI/CreatePage.md`
Source sections: 13. Edit and rerun contract, 14. Submission contract, 16. Failure and empty-state rules, 18. Testing requirements

Why: As a task author, I can edit or rerun an existing MoonMind.Run and get a reconstructed draft that preserves objective text, attachments, templates, dependencies, runtime options, and untouched attachment refs unless I change them.

Scope:
- Use the authoritative task input snapshot as the source for edit and rerun draft reconstruction.
- Reconstruct objective text, objective-scoped attachments, step instructions, step-scoped attachments, runtime settings, publish settings, templates, dirty state, and dependencies.
- Differentiate existing persisted attachments from new local files in state and UI.
- Support keep, remove, add, and replace flows for persisted attachments.
- Preserve untouched attachment refs by default during rerun.
- Fail explicitly if attachment targeting or bindings cannot be reconstructed.
- Cover edit reconstruction, rerun preservation, and no silent drop/duplicate behavior in tests.

Out of scope:
- Initial create page route setup.
- Jira browser implementation.
- Changing the execution API into a non-task workflow type.

Independent test:
- Load an existing task input snapshot into edit and rerun modes, preserve untouched persisted attachments, remove/add/replace attachments, simulate missing attachment bindings, and assert create/update/rerun payloads remain target-aware.

Acceptance criteria:
- Given I edit an existing MoonMind.Run, then the draft is reconstructed from the authoritative task input snapshot.
- Given I rerun an existing MoonMind.Run, then objective text, objective attachments, step instructions, step attachments, runtime and publish settings, applied templates and dirty state, and dependencies are reconstructed when they remain editable.
- Given persisted attachments exist, then they render distinctly from newly selected local files.
- Given I do not touch persisted attachments during rerun, then their refs survive the round trip without being dropped or duplicated.
- Given I remove, add, or replace an attachment, then only the authored target changes and unrelated draft state remains intact.
- Given one or more attachment bindings cannot be reconstructed, then edit or rerun fails explicitly rather than silently dropping attachments.

Dependencies: STORY-002, STORY-003, STORY-004, STORY-005.

Risks or open questions:
- Historical snapshots with incomplete attachment target data must fail clearly or be handled by a documented cutover rather than silently reconstructed.

Needs clarification:
- None.

Owned coverage:
- DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-023, DESIGN-REQ-025

## Coverage Matrix

| Coverage ID | Owning Stories |
| --- | --- |
| DESIGN-REQ-001 | STORY-001 |
| DESIGN-REQ-002 | STORY-001 |
| DESIGN-REQ-003 | STORY-001, STORY-006 |
| DESIGN-REQ-004 | STORY-001, STORY-004 |
| DESIGN-REQ-005 | STORY-002, STORY-007 |
| DESIGN-REQ-006 | STORY-002, STORY-005, STORY-007 |
| DESIGN-REQ-007 | STORY-002 |
| DESIGN-REQ-008 | STORY-002 |
| DESIGN-REQ-009 | STORY-002 |
| DESIGN-REQ-010 | STORY-003, STORY-006 |
| DESIGN-REQ-011 | STORY-003 |
| DESIGN-REQ-012 | STORY-003, STORY-006 |
| DESIGN-REQ-013 | STORY-004 |
| DESIGN-REQ-014 | STORY-004 |
| DESIGN-REQ-015 | STORY-004, STORY-006 |
| DESIGN-REQ-016 | STORY-005 |
| DESIGN-REQ-017 | STORY-006 |
| DESIGN-REQ-018 | STORY-006 |
| DESIGN-REQ-019 | STORY-007 |
| DESIGN-REQ-020 | STORY-007 |
| DESIGN-REQ-021 | STORY-005, STORY-007 |
| DESIGN-REQ-022 | STORY-003, STORY-006 |
| DESIGN-REQ-023 | STORY-005, STORY-006, STORY-007 |
| DESIGN-REQ-024 | STORY-002, STORY-005, STORY-006 |
| DESIGN-REQ-025 | STORY-002, STORY-003, STORY-005, STORY-006, STORY-007 |

## Dependencies Between Stories

- STORY-001 `create-page-shell` depends on: None.
- STORY-002 `step-attachment-targets` depends on: STORY-001.
- STORY-003 `preset-reapply-state` depends on: STORY-002.
- STORY-004 `execution-options` depends on: STORY-001.
- STORY-005 `image-upload-submit` depends on: STORY-002.
- STORY-006 `jira-import-targets` depends on: STORY-002, STORY-003, STORY-005.
- STORY-007 `edit-rerun-reconstruction` depends on: STORY-002, STORY-003, STORY-004, STORY-005.

## Out Of Scope

- Creating or modifying `spec.md` files; this breakdown is a handoff for later `/speckit.specify`.
- Creating directories under `specs/`; specify owns feature directory creation.
- Creating Jira issues; `jira` output mode means the stories are shaped for downstream Jira creation.
- Implementing the Create page; downstream Moon Spec plan, tasks, implement, and verify phases own code changes.
- Turning the page into a generic workflow builder, image editor, Jira-native surface, browser-direct integration surface, or binary transport layer.

## Recommended First Story

Run `/speckit.specify` first for `STORY-001` (`create-page-shell`) because it establishes the canonical route, hosting, single-form section order, and MoonMind API boundary that later attachment, preset, Jira, execution, and rerun stories build on.

## Coverage Gate

PASS - every major design point is owned by at least one story.
