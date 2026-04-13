# Create Page

Status: Proposed
Owners: MoonMind Engineering
Last updated: 2026-04-11

## 1. Purpose

Define the canonical desired-state design for the MoonMind Create page.

The Create page is the single task-composition surface for:

- authoring manual task steps
- applying reusable task presets
- selecting run dependencies
- configuring runtime, repository, publish, and schedule options
- importing Jira story text into either:
  - a step's `Instructions` field, or
  - the preset `Feature Request / Initial Instructions` field

This document describes the page as it exists today and extends it with the Jira browsing and selection experience.

---

## 2. Related docs

- `docs/UI/MissionControlArchitecture.md`
- `docs/UI/MissionControlStyleGuide.md`
- `docs/UI/TypeScriptSystem.md`
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
- manual entry must remain first-class even when presets or Jira are unavailable
- browser clients must only call MoonMind APIs; they must never call Jira directly

Important distinctions:

- **primary step instructions** are the default manual task objective
- **preset feature request / initial instructions** are the preferred objective source when present
- **applied preset steps** are expanded step blueprints, not live links to the preset definition
- **imported Jira text** is a one-time copy into a target field, not a live sync

---

## 4. Route and hosting model

The canonical Create page route is:

- `/tasks/new`

Rules:

- `/tasks/new` is the canonical route
- the page is server-hosted by FastAPI and rendered by the Mission Control React/Vite UI
- runtime config is generated server-side and passed through the boot payload
- all page actions must go through MoonMind REST APIs

Representative implementation surfaces:

- page entrypoint: `frontend/src/entrypoints/task-create.tsx`
- runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`

---

## 5. Current baseline this design preserves

The Create page already supports all of the following, and this design keeps them as first-class behaviors:

1. a multi-step editor with:
   - one required primary step
   - add / remove / reorder controls
   - per-step instructions
   - optional per-step skill selection
   - optional per-step required-capability CSV
   - optional per-step skill args JSON
2. a task preset area with:
   - preset selection
   - a preset objective field labeled `Feature Request / Initial Instructions`
   - preset expansion into steps
   - saving the current step draft as a preset
3. a dependency picker for existing `MoonMind.Run` prerequisites
4. runtime and provider selection
5. model and effort selection
6. repository and branch configuration
7. publish-mode selection
8. optional policy-gated attachment selection
9. priority and max-attempt controls
10. a `Propose Tasks` toggle
11. immediate, deferred, and recurring schedule modes
12. submission through the Temporal-backed create endpoint
13. automatic task-input artifact fallback when the inline submission payload is too large

This design does not replace the current Create page mental model. It extends it.

---

## 6. Canonical page model

The page is a single composition form with these canonical sections, in this order:

| Order | Section | Purpose |
| --- | --- | --- |
| 1 | Header | Identify the page as task creation |
| 2 | Steps | Author the task plan directly |
| 3 | Task Presets | Apply reusable step blueprints and define preset objective text |
| 4 | Dependencies | Block the new task on existing runs |
| 5 | Execution context | Runtime, provider, model, effort, repo, branches, publish mode |
| 6 | Attachments | Optional input files when enabled by policy |
| 7 | Execution controls | Priority, max attempts, propose tasks |
| 8 | Schedule | Immediate, once, deferred minutes, recurring |
| 9 | Submit | Create the task |

The Jira browser is not its own top-level page section. It is a shared secondary instruction-source surface that can be invoked from the Steps and Task Presets sections.

---

## 7. Step editor contract

### 7.1 Step list

The step editor must render a list of step cards.

Rules:

- the first card is always **Step 1 (Primary)**
- users may add, remove, and reorder steps
- reordering changes authored order only; it does not create dependency edges between steps
- the page must always remain valid with exactly one step card present

### 7.2 Step fields

Each step card must expose:

- `Instructions`
- `Skill (optional)`
- `Skill Required Capabilities (optional CSV)`
- `Skill Args (optional JSON object)` when a non-empty explicit skill is selected

Rules:

- the primary step must contain instructions or an explicit skill
- when any additional step is present, the primary step must contain instructions
- non-primary steps may omit instructions to continue from the task objective
- non-primary steps may omit skill to inherit the primary-step skill defaults

### 7.3 Template-bound steps

Preset-expanded steps may carry template step identity.

Rules:

- a template-expanded step remains template-bound only while its authored instructions still match the template-provided instructions
- any manual edit to a template-bound step's instructions detaches that step from the template binding for instruction identity purposes
- importing Jira text into a step counts as a manual instruction edit

---

## 8. Task preset contract

### 8.1 Preset area

The preset area must remain optional.

It must expose:

- `Preset`
- `Feature Request / Initial Instructions`
- `Apply`
- `Save Current Steps as Preset` when preset saving is enabled
- status text describing preset loading and apply outcomes

### 8.2 Preset application

Applying a preset expands blueprint steps into the step list.

Rules:

- when the form still contains only the initial empty default step, applying a preset may replace that placeholder step set
- otherwise, applying a preset appends expanded preset steps to the existing draft
- preset input values may be resolved from:
  - explicit preset objective text
  - remembered prior input values
  - preset defaults
  - current draft instructions
  - repository draft values
- applying a preset must be explicit; selecting a preset alone must not modify the step list

### 8.3 Preset objective text

`Feature Request / Initial Instructions` is the preset-owned objective source.

Rules:

- when non-empty, it is preferred over the primary step instructions for task objective resolution
- it is the correct target when the user wants Jira story text to drive a preset-oriented task flow
- changing this field must never silently mutate already-expanded preset steps

### 8.4 Preset reapply behavior

The page must distinguish between:

- selecting or editing preset inputs, and
- applying or reapplying the preset

Rules:

- when preset inputs change after a preset has already been applied, the page must mark the preset state as **needs reapply**
- the page must surface a clear `Reapply preset` action when the preset is dirty
- reapplying the preset must be explicit
- the page must not automatically overwrite expanded steps when the preset objective field changes, including when the change came from Jira import

---

## 9. Dependency contract

The dependency area must remain a bounded picker for existing `MoonMind.Run` executions.

Rules:

- users may add up to 10 direct dependencies
- duplicate dependencies must be rejected client-side
- dependency fetch failure must not block manual task creation
- dependency selection is independent from Jira and presets

---

## 10. Execution context contract

The Create page must preserve the existing execution-context controls.

Required controls:

- `Runtime`
- `Provider profile` when profiles exist for the selected runtime
- `Model`
- `Effort`
- `GitHub Repo`
- `Starting Branch (optional)`
- `Target Branch (optional)`
- `Publish Mode`

Rules:

- runtime defaults come from server-provided runtime config
- provider-profile options are runtime-specific
- resolver-style skills may still force publish mode to `none`
- repository validation rules remain unchanged by Jira integration
- Jira import must never bypass or weaken repository validation

---

## 11. Attachment, execution-control, and schedule contract

This design preserves the current controls for:

- attachments when enabled by policy
- priority
- max attempts
- `Propose Tasks`
- schedule mode and schedule parameters

Rules:

- Jira integration must not change attachment policy
- Jira integration must not change schedule semantics
- if attachment selection is visible but submission support is still unavailable for the current backend path, the page must fail fast with the current explicit message rather than silently dropping files

---

## 12. Objective resolution and title derivation

The Create page must preserve a single canonical objective-resolution rule.

The resolved task objective is determined in this order:

1. preset `Feature Request / Initial Instructions`
2. primary step `Instructions`
3. the most recent applied preset input that semantically aliases a feature request or request field

Rules:

- importing Jira text into the preset objective field overrides the primary step for objective resolution
- importing Jira text into the primary step affects the resolved objective only when the preset objective field is empty
- importing Jira text into a non-primary step does not change the resolved task objective
- explicit task title derivation should continue to use the first non-empty line of the resolved objective text

---

## 13. Jira integration: product role

Jira integration exists to source instruction text into the Create page.

It is not intended to:

- create MoonMind tasks automatically on issue selection
- replace the step editor
- replace presets
- change the submission API shape into a Jira-native workflow type
- make the browser talk directly to Jira

Primary Jira use cases:

1. browse a Jira board by column while composing a task
2. inspect a story before importing it
3. copy normalized story text into a chosen target field
4. use a Jira story to seed preset objective text
5. use a Jira story to seed the primary step or any secondary step instructions

---

## 14. Jira integration: runtime config contract

The Create page may expose Jira only when runtime config explicitly enables it.

Representative desired-state config shape:

```json
{
  "sources": {
    "jira": {
      "connections": "/api/jira/connections",
      "projects": "/api/jira/projects",
      "boards": "/api/jira/boards",
      "columns": "/api/jira/boards/{boardId}/columns",
      "issues": "/api/jira/boards/{boardId}/issues",
      "issue": "/api/jira/issues/{issueKey}"
    }
  },
  "system": {
    "jiraIntegration": {
      "enabled": true,
      "defaultProjectKey": "",
      "defaultBoardId": "",
      "rememberLastBoardInSession": true
    }
  }
}
```

Rules:

- the presence of `sources.jira` alone is not sufficient; Jira entry points should only render when `system.jiraIntegration.enabled` is true
- all Jira URLs must remain MoonMind API endpoints
- browser clients must not embed Jira credentials or Jira-domain knowledge beyond the documented response shapes

---

## 15. Jira integration: server API contract

The browser-facing API should return Create-page-ready data.

### 15.1 Column contract

Jira columns are board-specific.

Rules:

- the MoonMind API must resolve columns from Jira board configuration
- the MoonMind API, not the browser, is responsible for translating Jira status-to-column mapping into a board-column model
- the browser must render board columns in board order

Representative column response:

```json
{
  "board": {
    "id": "42",
    "name": "MoonMind Delivery",
    "projectKey": "MM"
  },
  "columns": [
    { "id": "todo", "name": "To Do", "count": 12 },
    { "id": "doing", "name": "Doing", "count": 4 },
    { "id": "done", "name": "Done", "count": 30 }
  ]
}
```

### 15.2 Issue-list contract

The issue-list endpoint must support board browsing by column.

Representative response:

```json
{
  "boardId": "42",
  "columns": [
    { "id": "todo", "name": "To Do" },
    { "id": "doing", "name": "Doing" },
    { "id": "done", "name": "Done" }
  ],
  "itemsByColumn": {
    "todo": [
      {
        "issueKey": "MM-123",
        "summary": "Add Jira import to Create page",
        "issueType": "Story",
        "statusName": "Selected for Development",
        "assignee": "Ada",
        "updatedAt": "2026-04-10T19:30:00Z"
      }
    ]
  }
}
```

Rules:

- the browser must not infer column membership from raw issue status text
- the issue list may optionally support `q=` filtering by issue key or summary
- empty columns must still be renderable

### 15.3 Issue-detail contract

The issue-detail endpoint must return normalized story content suitable for preview and import.

Representative response:

```json
{
  "issueKey": "MM-123",
  "url": "https://jira.example.com/browse/MM-123",
  "summary": "Add Jira import to Create page",
  "issueType": "Story",
  "column": { "id": "doing", "name": "Doing" },
  "status": { "id": "10001", "name": "In Progress" },
  "descriptionText": "As a user, I want to browse Jira stories by column...",
  "acceptanceCriteriaText": "Given a board... When I choose a column... Then I can import the story into a task field.",
  "recommendedImports": {
    "presetInstructions": "MM-123: Add Jira import to Create page\n\nAs a user, I want to browse Jira stories by column...",
    "stepInstructions": "Complete Jira story MM-123: Add Jira import to Create page\n\nDescription\nAs a user, I want to browse Jira stories by column...\n\nAcceptance criteria\nGiven a board..."
  }
}
```

Rules:

- MoonMind must normalize Jira rich text before returning it to the browser
- the browser must consume normalized text, not parse Jira rich-text formats itself
- the response should include target-specific recommended import text so the client can stay simple and consistent

---

## 16. Jira integration: shared browser surface

The Jira experience must be implemented as one shared instruction-source surface.

Canonical posture:

- one shared Jira browser may be open at a time
- it opens from a target field, but it is not permanently embedded inside every field
- it must preserve the rest of the draft while open

Preferred UI form:

- a modal or side drawer titled `Browse Jira story`

The Jira browser must show:

1. current import target
2. Jira connection selector when more than one connection exists
3. project selector when needed
4. board selector
5. column tabs or a segmented column control
6. issue list for the active column
7. an issue preview panel
8. import-mode selector
9. explicit import actions

Rules:

- opening the browser from a field preselects that field as the target
- the browser may allow target switching while open
- selecting an issue must not write into the draft automatically
- import requires an explicit user action

---

## 17. Jira integration: target model

Jira import must support these targets:

- preset objective field: `Feature Request / Initial Instructions`
- any step's `Instructions` field, including the primary step

Representative UI target model:

```ts
type JiraImportTarget =
  | { kind: "preset-objective" }
  | { kind: "step-instructions"; stepLocalId: string };
```

Rules:

- the target selected at browser-open time is the default target
- the browser should display the target explicitly so the user always knows where the import will land
- switching the target inside the browser must not clear the selected Jira issue

---

## 18. Jira integration: import modes

The browser must support target-aware import modes.

Minimum modes:

- `Preset brief` — summary plus description
- `Execution brief` — summary plus description plus acceptance criteria
- `Description only`
- `Acceptance criteria only`

Default mode by target:

- preset objective target defaults to `Preset brief`
- step-instructions target defaults to `Execution brief`

Rules:

- the import preview must update live as the user changes mode
- the user must be able to override the default mode before import
- import mode affects only the copied text, not the selected issue identity

---

## 19. Jira integration: write semantics

### 19.1 Preset-objective target

When the target is the preset objective field:

- importing must update `Feature Request / Initial Instructions`
- importing must not directly rewrite the step list
- if a preset has already been applied, the page must mark the preset as needing reapply

### 19.2 Step-instructions target

When the target is a step instructions field:

- importing must write directly into that step's `Instructions`
- importing into the primary step may satisfy the primary-step instruction requirement
- importing into a template-bound step detaches the step from template instruction identity if the imported text differs from the template instructions

### 19.3 Replace and append

The browser must support two explicit write actions:

- `Replace target`
- `Append to target`

Rules:

- `Replace target` is the default action
- `Append to target` must preserve the existing field text and add a clear separator before the imported Jira text
- neither action may run automatically on issue selection

---

## 20. Jira integration: provenance and affordances

The Create page should retain lightweight, field-level source provenance after import.

Representative desired-state provenance model:

```ts
interface JiraImportProvenance {
  source: "jira";
  issueKey: string;
  boardId: string;
  columnId: string;
  importedAt: string;
  mode: "preset-brief" | "execution-brief" | "description-only" | "acceptance-only";
}
```

Rules:

- provenance is advisory UI metadata, not a live binding
- the page should render a subtle chip such as `Jira: MM-123` near an imported field
- reopening the Jira browser from an imported field should prefer the prior issue selection when possible
- clearing a field does not require a Jira call

Submission behavior:

- Jira provenance may be carried as advisory metadata if and when the backend accepts it
- absence of submitted provenance must not block create
- create must remain compatible with the existing task payload shape

---

## 21. Failure and empty-state rules

Jira must be an additive capability only.

Rules:

- if Jira integration is disabled, the page must hide Jira entry points and remain fully usable
- if Jira fetch fails, the failure must remain local to the Jira browser and must not corrupt the draft
- the user must be able to close the Jira browser and continue manual authoring without losing form state
- empty project, board, column, or issue states must be rendered explicitly
- if the selected issue cannot be fetched, the page must keep the current draft untouched

Representative empty and failure copy:

- `No Jira boards available.`
- `This board has no columns.`
- `No issues found in this column.`
- `Failed to load Jira stories. You can continue creating the task manually.`

---

## 22. Submission invariants

The Create page submit flow must remain fundamentally unchanged.

Rules:

- Jira import changes authored field content only
- the task is still submitted through the existing Temporal-backed create endpoint
- objective resolution order remains unchanged except for the fact that Jira can now populate either of the existing objective sources
- oversized task input must still use the existing artifact fallback flow
- Jira must not introduce a separate create endpoint or a separate task type

Practical consequence:

- importing Jira into the preset objective field feeds the existing preset-objective path
- importing Jira into the primary step feeds the existing manual-step path
- importing Jira into a secondary step affects only that step unless the same text is also placed into the preset objective or primary step

---

## 23. Accessibility and interaction rules

The Jira browser and new field affordances must meet the same accessibility bar as the rest of Mission Control.

Rules:

- all open, close, target, and import actions must be keyboard accessible
- the browser title must identify the current target context
- column controls must be navigable by keyboard and expose active state clearly
- issue rows must expose key plus summary for screen-reader scanning
- importing must move focus predictably back to the updated field or to an explicit success notice

---

## 24. Testing requirements

The Create page test suite should cover the following behaviors:

1. Jira entry points are hidden when integration is disabled
2. board columns load and render in board order
3. issue lists switch correctly by column
4. selecting an issue does not mutate the draft until import is confirmed
5. importing into the preset objective field updates only that field
6. importing into the primary step updates that step and satisfies instruction validation
7. importing into a secondary step updates only that step
8. importing into a template-bound step detaches template instruction identity when appropriate
9. changing the preset objective through Jira after preset apply marks the preset as needing reapply
10. Jira API failures do not block manual task creation
11. create submission still uses the existing endpoint and objective resolution order

---

## 25. Summary of desired-state posture

The Create page remains a single, task-first composition form.

Jira is added as a reusable instruction-source browser that can target either:

- a step's `Instructions`, or
- the preset `Feature Request / Initial Instructions`

That is the entire product posture.

The page does **not** become Jira-native. It stays MoonMind-native, while letting Jira provide high-value source text exactly where users already compose work.
