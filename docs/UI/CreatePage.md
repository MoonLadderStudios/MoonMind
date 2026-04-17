# Create Page

Status: Proposed
Owners: MoonMind Engineering
Last updated: 2026-04-16

## 1. Purpose

This document defines the canonical desired-state contract for the MoonMind Create page.

The Create page is the single task-authoring surface for:

- composing manual task steps
- attaching structured input images to the task objective or to individual steps
- applying reusable task presets
- importing Jira text and, when allowed, Jira images into declared draft targets
- selecting run dependencies
- configuring runtime, repository, publish, and schedule options
- creating, editing, and rerunning task-shaped Temporal executions

This document is declarative. It defines the contract the product must satisfy. It is not an implementation changelog or rollout log.

---

## 2. Related docs

- `docs/UI/MissionControlArchitecture.md`
- `docs/UI/MissionControlStyleGuide.md`
- `docs/UI/TypeScriptSystem.md`
- `docs/Tasks/ImageSystem.md`
- `docs/Tasks/TaskArchitecture.md`
- `docs/Tasks/AgentSkillSystem.md`
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`

---

## 3. Product stance

The Create page is a task authoring surface, not a generic workflow builder.

Core posture:

- the task is the primary product object
- steps are the canonical authored execution units
- presets are reusable step blueprints and objective helpers, not a separate task type
- Jira is an external instruction source, not a runtime, not an execution substrate, and not a primary task source
- input images are structured task inputs, not pasted binary content and not part of the instruction text body
- browser clients call only MoonMind APIs; they never call Jira, object storage, or model providers directly
- manual authoring remains first-class even when presets, Jira, or image upload are unavailable
- edit and rerun must preserve the authored task draft, including attachment targeting, unless the user explicitly changes it

Important distinctions:

- **objective text** is resolved from existing task-authoring fields; it is still text
- **objective-scoped images** are structured inputs attached to the task objective target
- **step-scoped images** are structured inputs attached to a specific step target
- **applied preset steps** are expanded step blueprints, not live bindings to a preset definition
- **imported Jira text** is a one-time copy into a text field, not a live sync
- **imported Jira images** are structured attachment selections, not inline embedded images

---

## 4. Route and hosting model

The canonical Create page route is:

- `/tasks/new`

Rules:

- `/tasks/new` is the canonical route
- compatibility aliases may exist, but they must redirect to the canonical route and must not define separate product behavior
- the page is server-hosted by FastAPI and rendered by the Mission Control React/Vite UI
- runtime configuration is generated server-side and passed through the boot payload
- all page actions go through MoonMind REST APIs
- artifact upload, preview, and download all remain behind MoonMind-controlled API surfaces

Representative implementation surfaces:

- page entrypoint: `frontend/src/entrypoints/task-create.tsx`
- runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`

---

## 5. Canonical page model

The page is a single composition form with these canonical sections, in this order:

| Order | Section | Purpose |
| --- | --- | --- |
| 1 | Header | Identify the page as task creation, edit, or rerun |
| 2 | Steps | Author the execution plan directly, including step-scoped image inputs |
| 3 | Task Presets | Apply reusable step blueprints and define preset objective text and objective-scoped image inputs |
| 4 | Dependencies | Block the new run on existing `MoonMind.Run` executions |
| 5 | Execution context | Runtime, provider, model, effort, repository, branches, publish mode |
| 6 | Execution controls | Priority, max attempts, propose tasks |
| 7 | Schedule | Immediate, once, deferred minutes, recurring |
| 8 | Submit | Create, save changes, or rerun |

Rules:

- there is no detached, page-wide “image bucket”
- every selected attachment belongs to an explicit target
- the shared secondary surfaces are:
  - the Jira browser
  - provider/profile selectors
  - attachment preview and removal affordances
- image selection belongs with the field it informs, not with unrelated page controls

---

## 6. Draft model

The browser draft is step-first and target-aware.

Representative browser model:

```ts
type AttachmentTarget =
  | { kind: "objective" }
  | { kind: "step"; stepLocalId: string };

interface DraftAttachment {
  localId: string;
  target: AttachmentTarget;
  source: "local" | "artifact";
  status: "selected" | "uploading" | "uploaded" | "failed";
  artifactId?: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  previewUrl?: string | null;
  errorMessage?: string | null;
}

type PresetBindingStatus =
  | "bound"
  | "needs-reapply"
  | "partially-detached"
  | "flat-reconstructed"
  | "unavailable";

interface AppliedPresetBinding {
  bindingId: string;
  presetSlug: string;
  presetVersion: string;
  includePath: string[];
  expansionDigest: string;
  groupLabel: string;
  status: PresetBindingStatus;
  reapplySummary?: string;
  warning?: string;
}

type StepDraftSource =
  | { kind: "local" }
  | {
      kind: "preset-bound";
      bindingId: string;
      sourcePresetSlug: string;
      sourcePresetVersion: string;
      sourceBlueprintSlug: string;
      includePath: string[];
      provenance: Record<string, unknown>;
    }
  | {
      kind: "preset-detached";
      bindingId: string;
      detachedReason: "instructions-edited" | "attachments-edited" | "reordered" | "partial-selection" | "unknown";
    }
  | {
      kind: "flat-reconstructed";
      warning: string;
    };

interface StepDraft {
  localId: string;
  id: string;
  title: string;
  instructions: string;
  skillId: string;
  skillArgs: string;
  skillRequiredCapabilities: string;
  source: StepDraftSource;
  sourceInstructions?: string;
  attachments: DraftAttachment[];
  sourceAttachments: DraftAttachment[];
}

interface TaskDraft {
  presetObjectiveText: string;
  presetObjectiveAttachments: DraftAttachment[];
  steps: StepDraft[];
  appliedPresetBindings: AppliedPresetBinding[];
  runtime: string;
  providerProfile: string;
  model: string;
  effort: string;
  repository: string;
  startingBranch: string;
  targetBranch: string;
  publishMode: "none" | "branch" | "pr";
  dependencies: string[];
}
```

Rules:

- attachments are structured inputs, not part of the `Instructions` text value
- the UI may render image thumbnails directly under an instructions field so the relationship is visually clear
- the source of truth for attachment targeting is structured state, not text conventions
- the draft may contain both local-file selections and previously uploaded artifact-backed attachments
- the draft must distinguish:
  - an existing persisted attachment
  - a newly selected local file
  - an upload failure that has not yet been resolved
- `AppliedPresetBinding` is the browser-side source of truth for applied composed preset authoring state
- `StepDraft.source` records whether a step is local, preset-bound, preset-detached, or flat-reconstructed
- preset binding state is draft metadata for authoring, preview, reapply, save-as-preset, edit, and rerun; runtime submission still uses flattened resolved steps

---

## 7. Step editor contract

### 7.1 Step list

The step editor renders a list of step cards.

Rules:

- the first card is always **Step 1 (Primary)**
- users may add, remove, and reorder steps
- reordering changes authored order only; it does not create dependency edges between steps
- the page must remain valid with exactly one step card present
- each step card owns its own instructions and attachment state
- moving or reordering a step moves its attachments with it

### 7.2 Step fields

Each step card must expose:

- `Instructions`
- `Images` or `Input Attachments` when attachment policy is enabled
- `Skill (optional)`
- `Skill Args (optional JSON object)` when a non-empty explicit skill is selected
- `Skill Required Capabilities (optional CSV)` in Advanced Settings

Rules:

- the primary step must contain instructions or an explicit skill
- when any additional step is present, the primary step must contain instructions
- non-primary steps may omit instructions to continue from the task objective
- non-primary steps may omit skill to inherit primary-step skill defaults
- step attachments are visible in the same card as the step instructions they inform

### 7.3 Step attachment contract

A step may own zero or more attachments.

Rules:

- step attachments are submitted through `task.steps[n].inputAttachments`
- adding or removing a step attachment is a first-class authored change to that step
- the UI must show, per attachment:
  - filename
  - type
  - size
  - thumbnail preview when preview is supported
  - upload or error state when relevant
- the UI must allow removing a selected or persisted attachment before submit
- attachment order may be displayed, but execution semantics do not rely on visual ordering
- the same image must not become attached to another step implicitly because the user reordered steps, edited text, or applied a preset
- if the product supports drag-and-drop or paste, those gestures must still land on an explicit target step

### 7.4 Objective-scoped attachment target

The page also supports an objective-scoped attachment target.

This target exists for the preset-owned objective field rather than for a specific step.

Rules:

- objective-scoped attachments are submitted through `task.inputAttachments`
- they belong to the preset objective target, not to an anonymous page-wide bucket
- if presets are disabled, this target may be hidden entirely
- objective-scoped attachments are part of the task-level objective context
- they are not copied down into step attachments automatically

### 7.5 Preset-bound steps

Preset-expanded steps may carry preset-bound source identity.

Rules:

- a preset-expanded step remains preset-bound only while its authored instructions and attachment set still match the preset-authored blueprint input contract
- any manual edit to a preset-bound step's instructions changes `StepDraft.source` to `preset-detached`
- any manual edit to a preset-bound step's attachment set changes `StepDraft.source` to `preset-detached`
- `sourceAttachments` stores the preset-authored attachment set used for detachment comparisons
- importing Jira text or Jira images into a preset-bound step counts as a manual edit
- detached steps preserve authored content and are not overwritten by default reapply

---

## 8. Task preset contract

### 8.1 Preset area

The preset area remains optional.

It exposes:

- `Preset`
- `Feature Request / Initial Instructions`
- objective-scoped image inputs when attachment policy is enabled
- `Apply`
- `Save Current Steps as Preset` when preset saving is enabled
- status text describing preset load and apply outcomes

### 8.2 Preset application

Applying a preset asks the server to expand the preset into binding metadata,
grouped composition data, and flattened blueprint steps.

Rules:

- a preset is an authoring object that may include other presets
- execution uses the flattened resolved steps produced by expansion
- selecting a preset alone must not modify the draft
- preview renders grouped composition without inserting partial state into the draft
- a successful apply creates or updates an `AppliedPresetBinding`
- the apply result includes the expansion digest, include paths, blueprint slugs, flat steps, and per-step provenance needed for `StepDraft.source`
- when the form still contains only the initial empty default step, applying a preset may replace that placeholder step set
- otherwise, applying a preset appends expanded preset steps to the existing draft
- objective-scoped attachments are not silently generated by preset application unless the preset system explicitly defines them in a future contract
- preset application remains an explicit action
- expansion failures are non-mutating and must describe what prevented preview or apply

### 8.3 Preset objective contract

`Feature Request / Initial Instructions` is the preset-owned objective text source.

Rules:

- when non-empty, it is preferred over primary-step instructions for objective text resolution
- objective-scoped attachments are the matching structured input source for this field
- changing preset objective text or objective-scoped attachments must not silently rewrite already expanded steps

### 8.4 Reapply behavior

The page distinguishes between:

- changing preset inputs, and
- applying or reapplying the preset

Rules:

- when preset objective text changes after apply, the page marks the preset state as **needs reapply**
- when objective-scoped attachments change after apply, the page also marks the preset state as **needs reapply**
- the page surfaces a clear `Reapply preset` action when the preset is dirty
- reapply is explicit
- the page must not automatically overwrite expanded steps because preset inputs changed
- before reapply, the page discloses the exact effect: which still-bound steps will update, which detached steps will remain unchanged, and whether any binding metadata is unavailable
- by default, reapply updates still-bound steps and leaves preset-detached steps untouched
- reapply must not silently restore a detached source relationship for a manually edited step

---

## 9. Dependency contract

The dependency area remains a bounded picker for existing `MoonMind.Run` executions.

Rules:

- users may add up to 10 direct dependencies
- duplicate dependencies are rejected client-side
- dependency fetch failure must not block manual task creation
- dependency selection is independent from image attachments, Jira, and presets

---

## 10. Execution context contract

The Create page preserves these execution-context controls:

- `Runtime`
- `Provider profile` when profiles exist for the selected runtime
- `Model`
- `Effort`
- `GitHub Repo`
- `Starting Branch (optional)`
- `Target Branch (optional)`
- `Publish Mode`
- `Enable merge automation` when publish mode is `pr` for an ordinary task

Rules:

- runtime defaults come from server-provided runtime configuration
- attachment policy also comes from server-provided runtime configuration
- provider-profile options are runtime-specific
- repository validation rules are unaffected by attachments or Jira
- resolver-style skills may still force publish mode to `none`
- merge automation is available only for ordinary PR-publishing tasks
- when merge automation is selected, the submitted task creation payload must
  preserve `publishMode=pr`, preserve `task.publish.mode=pr`, and include
  `mergeAutomation.enabled=true`
- when publish mode is `branch` or `none`, or when the selected task is a direct
  `pr-resolver` or `batch-pr-resolver` task, merge automation must be hidden or
  disabled and must not be submitted
- the merge automation copy must explain that MoonMind waits for the PR
  readiness gate and then uses `pr-resolver`; it must not imply direct
  auto-merge or a bypass around resolver behavior
- Jira Orchestrate preset behavior remains explicit and unchanged by this
  Create page option; changing Jira Orchestrate to parent-owned PR publishing
  requires a separate story
- repository validation rules remain unchanged by Jira integration
- Jira import must never bypass or weaken repository validation
- image upload must never bypass or weaken repository validation, publish validation, or runtime gating

---

## 11. Attachment policy and UX contract

Attachment behavior is policy-gated.

Representative runtime config shape:

```json
{
  "system": {
    "attachmentPolicy": {
      "enabled": true,
      "maxCount": 10,
      "maxBytes": 10485760,
      "totalBytes": 26214400,
      "allowedContentTypes": [
        "image/png",
        "image/jpeg",
        "image/webp"
      ]
    }
  }
}
```

Rules:

- when policy is disabled, all attachment entry points are hidden
- when policy permits only image MIME types, the UI should use an image-specific label such as `Images`
- validation must happen both before upload and at submit time
- the browser must fail fast and visibly when:
  - count limit is exceeded
  - single-file size limit is exceeded
  - total size limit is exceeded
  - content type is unsupported
  - upload fails
- the browser must not silently drop a selected image
- the user must be able to remove a failed or invalid image without losing unrelated draft state
- previews are advisory UI affordances; preview failure must not corrupt the draft

Desired-state UX affordances:

- file picker
- drag-and-drop where supported
- paste-from-clipboard where supported
- thumbnail preview for supported images
- keyboard-accessible remove and retry actions
- a concise per-target attachment summary

---

## 12. Jira integration contract

### 12.1 Product role

Jira exists to source task inputs into the Create page.

It is not intended to:

- create MoonMind tasks automatically on issue selection
- replace the step editor
- replace presets
- change the task submission API shape into a Jira-native workflow type
- make the browser talk directly to Jira

### 12.2 Supported targets

The Jira browser supports these targets:

- preset objective text
- preset objective attachments
- any step’s instructions
- any step’s attachments

Representative target model:

```ts
type JiraImportTarget =
  | { kind: "preset-objective-text" }
  | { kind: "preset-objective-attachments" }
  | { kind: "step-text"; stepLocalId: string }
  | { kind: "step-attachments"; stepLocalId: string };
```

Rules:

- opening the browser from a field preselects its matching target
- the browser must display the current target explicitly
- switching targets inside the browser must not clear the selected issue

### 12.3 Text and image import semantics

Rules:

- selecting a Jira issue never mutates the draft automatically
- text import remains explicit and supports `Replace target text` and `Append to target text`
- image import remains explicit and supports selecting which supported images to add
- imported Jira images become structured attachments on the selected target
- imported Jira images are not injected into instruction text as markdown, HTML, or inline data
- when importing into a preset-bound step, text and attachment imports both count as manual customization

### 12.4 Preset interaction

Rules:

- importing Jira text or images into the preset objective target marks an already-applied preset as needing reapply
- importing into a step target does not mutate preset objective state

---

## 13. Edit and rerun contract

The Create page also serves as the edit and rerun surface for `MoonMind.Run` executions.

Rules:

- edit and rerun reconstruct the draft from the authoritative task input snapshot
- reconstructed draft state includes:
  - objective text
  - objective-scoped attachments
  - step instructions
  - step-scoped attachments
  - runtime and publish settings
  - applied preset bindings and their dirty or reapply state
  - per-step source state when binding metadata is recoverable
  - dependencies that remain part of the editable contract
- existing persisted attachments must be rendered distinctly from newly selected local files
- the user must be able to:
  - keep an existing attachment
  - remove an existing attachment
  - add a new attachment
  - replace one attachment by removing the old and adding the new
- rerun preserves original attachment refs by default unless the user edits them
- untouched attachments must survive round-trips through edit and rerun without being silently dropped or duplicated
- when binding state is recoverable, edit and rerun preserve `AppliedPresetBinding` and `StepDraft.source`
- when only flat reconstruction is available, edit and rerun show a clear flat reconstruction warning and set affected steps to `flat-reconstructed`
- flat reconstruction must not claim that steps remain preset-bound

---

## 13.1 Save-as-preset with composed drafts

Save-as-preset preserves intact composition by default.

Rules:

- intact preset-bound groups are saved as composition when source provenance and include paths still match the applied binding
- detached, partial, reordered, or flat-reconstructed steps are serialized as concrete steps
- flattening intact composition before save requires an explicit advanced action
- save-as-preset must disclose whether the saved preset preserves composition or stores flattened steps

---

## 14. Submission contract

The Create page submit flow remains artifact-first.

Rules:

- local images upload to the artifact system before create, edit, or rerun is submitted
- the browser submits structured attachment refs, not raw binary payloads, to the execution create/update API
- the control plane stores an authoritative task input snapshot that preserves attachment targeting
- oversized task text continues to use existing artifact fallback behavior for text payloads
- attachment upload completion is required before the execution becomes eligible to start
- attachment selection alone does not create a task; submit remains explicit

Representative task-shaped payload:

```json
{
  "type": "task",
  "payload": {
    "repository": "owner/repo",
    "task": {
      "instructions": "Resolved task objective text.",
      "inputAttachments": [
        {
          "artifactId": "art_objective_123",
          "filename": "overview.png",
          "contentType": "image/png",
          "sizeBytes": 48213
        }
      ],
      "steps": [
        {
          "id": "step-1",
          "instructions": "Inspect the screenshot and identify the bug.",
          "inputAttachments": [
            {
              "artifactId": "art_step1_456",
              "filename": "bug.png",
              "contentType": "image/png",
              "sizeBytes": 72109
            }
          ]
        },
        {
          "id": "step-2",
          "instructions": "Implement the fix and verify the result."
        }
      ]
    }
  }
}
```

Rules:

- `task.inputAttachments` is the objective-scoped target
- `task.steps[n].inputAttachments` is the step-scoped target
- absence of attachments is valid
- the meaning of an attachment is defined by its target, not by filename conventions

---

## 15. Objective resolution and title derivation

The page preserves a canonical objective-resolution rule for text.

The resolved objective text is determined in this order:

1. preset `Feature Request / Initial Instructions`
2. primary step `Instructions`
3. the most recent applied preset input that semantically aliases a feature request or request field

Rules:

- importing Jira text into the preset objective field overrides primary-step text for objective text resolution
- importing Jira text into the primary step affects resolved objective text only when preset objective text is empty
- importing Jira text into a non-primary step does not change resolved objective text
- explicit task title derivation continues to use the first non-empty line of the resolved objective text

Attachment rules:

- objective-scoped attachments are always part of the task-level objective input context
- primary-step attachments may also participate in task-level objective input context because the primary step is the default authored task objective
- non-primary step attachments do not affect task title derivation
- non-primary step attachments do not become task-level objective inputs unless the planner or runtime explicitly promotes them for a derived purpose

---

## 16. Failure and empty-state rules

Rules:

- if attachment policy is disabled, the page hides attachment entry points and remains fully usable
- if an upload fails, the failure remains local to the affected target and the rest of the draft remains intact
- if attachment preview fails, metadata remains visible and the user can still remove the attachment
- if edit or rerun cannot reconstruct attachments, the page must fail explicitly rather than silently dropping them
- if Jira is unavailable, the user can close the Jira browser and continue manual authoring without losing draft state
- if the selected Jira issue cannot be fetched, the page must not mutate the draft
- create, edit, and rerun must never proceed with silently discarded attachments

Representative copy:

- `Image upload failed. Remove the image or retry before submitting.`
- `This runtime does not currently allow image inputs.`
- `The task draft could not be reconstructed because one or more attachment bindings were missing.`
- `Failed to load Jira issue. You can continue creating the task manually.`

---

## 17. Accessibility and interaction rules

Rules:

- all open, close, target, upload, remove, retry, and import actions must be keyboard accessible
- image preview controls must expose meaningful labels to assistive technology
- step cards must clearly identify when attachments are present
- the Jira browser title must identify the current import target
- after importing text or images from Jira, focus must return predictably to the updated field or to an explicit success notice
- validation errors must be associated with the specific target that failed

---

## 18. Testing requirements

The Create page test suite should cover:

1. attachment entry points are hidden when policy is disabled
2. image validation enforces count, per-file size, total size, and content type
3. selecting images does not create hidden draft mutations on other targets
4. step-scoped images remain attached to the correct step through reorder operations
5. importing Jira text does not mutate the draft until import is confirmed
6. importing Jira images creates structured attachments on the selected target
7. importing into a preset-bound step detaches that step when appropriate
8. changing preset objective text or objective-scoped attachments after apply marks the preset as needing reapply
9. create submission uploads images before execution create
10. edit reconstructs persisted attachments correctly
11. rerun preserves untouched attachments by default
12. preview failure or upload failure does not corrupt unrelated draft state
13. submit fails explicitly when attachments are invalid or incomplete
14. preset preview renders grouped composition without mutating the draft
15. preset apply receives binding metadata, flattened steps, and per-step provenance
16. reapply discloses still-bound updates and detached skips before confirmation
17. save-as-preset preserves intact composition by default and uses explicit advanced flattening
18. edit and rerun preserve binding state when possible and warn when only flat reconstruction is available
19. degraded preset metadata, expansion, or reconstruction failures do not corrupt unrelated draft state

---

## 19. Summary

The Create page remains a single, task-first composition form.

Images are supported as explicit structured inputs attached either to:

- the preset objective target, or
- a specific step target

That is the desired-state contract.

The page does not become an image editor, a Jira-native surface, or a binary transport layer. It remains MoonMind-native and task-first while allowing users to author text and image inputs together in the same draft.
