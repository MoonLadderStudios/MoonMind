# Create Page

Status: Active desired-state contract  
Owners: MoonMind Engineering  
Last updated: 2026-05-02  
Canonical for: Mission Control task creation, edit, rerun, step authoring, schema-driven step configuration, and Create-page submission shaping

---

## 1. Purpose

This document defines the canonical desired-state contract for the MoonMind **Create page**.

The Create page is the primary Mission Control task-authoring surface for:

1. composing task steps;
2. selecting one **Step Type** per step;
3. configuring Tool, Skill, and Preset steps;
4. rendering schema-driven input controls for selected Tools, Skills, and Presets;
5. attaching structured input images or files to the task objective or individual steps;
6. importing Jira issue context into declared targets;
7. selecting dependencies;
8. configuring runtime, provider profile, repository, branch, publish, merge automation, and schedule options;
9. creating, editing, and rerunning task-shaped `MoonMind.Run` executions.

This document is declarative. It defines the product and contract behavior the Create page must satisfy. It is not an implementation log, migration checklist, or feature rollout plan.

---

## 2. Related docs

Use this document for Create-page behavior and browser draft/submission mapping.

Use related docs for system-level contracts:

- `docs/Steps/StepTypes.md` — canonical Tool / Skill / Preset taxonomy.
- `docs/Steps/SkillSystem.md` — canonical Skill-step, agent-skill catalog, resolution, and materialization model.
- `docs/Tasks/SkillAndPlanContracts.md` — executable Tool definitions, Tool input schemas, and plan execution.
- `docs/Tasks/TaskPresetsSystem.md` — Preset catalog, governance, versioning, and expansion service behavior.
- `docs/Tasks/TaskPublishing.md` — branch, publish, PR, and merge automation semantics.
- `docs/Tasks/ImageSystem.md` — attachment and image input behavior.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` — `MoonMind.AgentRun` and managed/external runtime execution.
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` — workflow creation, edit, rerun, and lifecycle behavior.
- `docs/UI/MissionControlArchitecture.md` — Mission Control shell and shared UI architecture.
- `docs/UI/TypeScriptSystem.md` — frontend TypeScript organization and generated contract discipline.

---

## 3. Product stance

The Create page is a task-authoring surface, not a generic workflow builder.

Core rules:

1. The **task** is the primary product object.
2. **Steps** are the canonical authored units of work.
3. Every authored step has exactly one user-facing **Step Type**.
4. The canonical Step Types are:
   - `Tool`
   - `Skill`
   - `Preset`
5. Tool and Skill steps are executable step types.
6. Preset steps are authoring-time placeholders that preview and apply into executable Tool and/or Skill steps before normal submission.
7. Schema-driven controls are the default way to configure typed step inputs.
8. Freeform JSON is an advanced fallback, not the primary UX.
9. Browser clients call only MoonMind APIs. They must not call Jira, GitHub, object storage, model providers, or agent runtimes directly.
10. Attachments are structured inputs, not pasted binary content and not inline instruction text.
11. Edit and rerun preserve the authored task draft, selected Step Types, type-specific payloads, attachments, and provenance unless the user explicitly changes them.
12. Runtime implementation terms such as Temporal Activity, task queue, capability placement, and adapter command must not be the primary user-facing discriminator for ordinary step authoring.

Important distinctions:

- **Tool** means a typed, schema-backed, policy-checked operation MoonMind can run directly.
- **Skill** means agent-facing behavior, reusable instructions, runtime context, and optional allowed tools for open-ended work.
- **Preset** means a reusable authoring template that expands into concrete executable steps.
- **Preset provenance** is metadata for audit, grouping, review, reconstruction, and explicit refresh. It must not control runtime correctness.
- **Skill args** are typed configuration for a selected Skill step. They are not Skill content, not prompt body, and not a secret store.
- **Tool inputs** are typed configuration for a selected Tool step. They are not arbitrary shell snippets.
- **Branch** is the single authored branch value. The Create page does not expose a separate `Target Branch` control.

---

## 4. Route and hosting model

The canonical Create page route is:

```text
/tasks/new
```

Rules:

1. `/tasks/new` is the canonical task creation route.
2. Compatibility aliases may exist, but they must redirect to the canonical route and must not define separate product behavior.
3. The page is server-hosted by FastAPI and rendered by the Mission Control React/Vite frontend.
4. Runtime configuration is generated server-side and passed through the boot payload.
5. All integration-backed page actions go through MoonMind REST APIs.
6. Artifact upload, preview, and download stay behind MoonMind-controlled API surfaces.
7. Repository branch lookup is a MoonMind API backed by GitHub branch discovery for the selected repository.
8. Jira issue browsing and import are MoonMind APIs backed by the configured Jira integration.
9. Tool discovery, dynamic Tool options, Skill descriptors, Skill input schemas, and Preset expansion are MoonMind APIs.
10. The browser must never directly call GitHub, Jira, object storage, agent runtimes, or model providers.

Representative implementation surfaces:

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/lib/temporalTaskEditing.ts
api_service/api/routers/task_dashboard_view_model.py
moonmind/workflows/tasks/task_contract.py
```

---

## 5. Canonical page model

The page is a single composition form.

Canonical sections:

| Order | Section | Purpose |
| --- | --- | --- |
| 1 | Header | Identify create, edit, or rerun mode and preserve source execution context. |
| 2 | Steps | Author ordered Tool, Skill, and Preset steps. |
| 3 | Repository and publishing bar | Select repository, branch, publish mode, and merge automation where applicable. |
| 4 | Dependencies | Block the new run on existing `MoonMind.Run` executions. |
| 5 | Execution context | Select runtime, provider profile, model, and effort. |
| 6 | Execution controls | Configure priority, max attempts, proposal behavior, and related run controls. |
| 7 | Schedule | Submit immediately or configure deferred/recurring execution. |
| 8 | Preset management | Optional management-only surface for saving or managing presets. |
| 9 | Submit | Validate, upload artifacts, create/update/rerun the task, and show submission status. |

Rules:

1. The Steps section is the primary authoring surface.
2. Preset use belongs inside the step editor through `Step Type = Preset`.
3. A separate Preset section, if present, is for management actions such as saving current steps as a preset, inspecting catalog metadata, or managing versions. It is not the canonical surface for choosing a preset to apply to the current task.
4. Repository, branch, publish mode, and merge automation authoring belong with the step authoring context and are not duplicated in Execution context.
5. The Create page exposes no separate `Target Branch` control.
6. There is no detached page-wide attachment bucket.
7. Every attachment belongs to an explicit target: task objective, a specific step, or a generated field that stores an artifact reference.
8. Manual authoring remains first-class when descriptor lookup, Jira import, branch lookup, tool discovery, or preset expansion is unavailable.

---

## 6. Browser draft model

The browser draft is step-first, target-aware, and Step-Type-discriminated.

Representative conceptual model:

```ts
type StepType = "tool" | "skill" | "preset";

type AttachmentTarget =
  | { kind: "objective" }
  | { kind: "step"; stepLocalId: string }
  | { kind: "generated-field"; stepLocalId: string; fieldPath: string };

interface DraftAttachment {
  localId: string;
  target: AttachmentTarget;
  source: "local" | "artifact" | "integration";
  status: "selected" | "uploading" | "uploaded" | "failed";
  artifactId?: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  previewUrl?: string | null;
  errorMessage?: string | null;
}

interface ToolDraft {
  id: string;
  version?: string;
  inputs: Record<string, unknown>;
  inputsText: string;
  descriptor?: StepConfigurationDescriptor | null;
  dynamicOptions?: Record<string, DynamicOptionState>;
}

interface SkillDraft {
  id: string;
  version?: string;
  args: Record<string, unknown>;
  argsText: string;
  requiredCapabilities: string[];
  descriptor?: StepConfigurationDescriptor | null;
  formMode: "schema" | "json";
}

interface PresetDraft {
  key: string;
  version?: string;
  inputValues: Record<string, string | boolean | number | null>;
  detail?: PresetDescriptor | null;
  preview?: PresetPreviewState | null;
  message?: string | null;
}

interface PresetProvenance {
  kind: "preset-derived";
  presetId: string;
  presetVersion: string;
  includePath?: string[];
  originalStepId?: string;
}

interface StepDraft {
  localId: string;
  id: string;
  title: string;
  stepType: StepType;
  instructions: string;
  inputAttachments: DraftAttachment[];
  source?: PresetProvenance | Record<string, unknown>;
  stepTypeMessage?: string | null;
  tool?: ToolDraft | null;
  skill?: SkillDraft | null;
  preset?: PresetDraft | null;
  generatedTool?: Record<string, unknown>;
  generatedSkill?: Record<string, unknown>;
}

interface RepositoryBranchCatalog {
  repository: string;
  status: "idle" | "loading" | "loaded" | "failed";
  defaultBranch?: string;
  options: Array<{ name: string; isDefault: boolean }>;
  errorMessage?: string | null;
}

interface TaskDraft {
  objectiveText: string;
  objectiveAttachments: DraftAttachment[];
  steps: StepDraft[];
  repository: string;
  branch: string;
  publishMode: "none" | "branch" | "pr";
  mergeAutomationEnabled: boolean;
  runtime: string;
  providerProfile: string;
  model: string;
  effort: string;
  dependencies: string[];
  schedule: Record<string, unknown>;
}
```

Implementation may store equivalent flat fields such as `toolId`, `toolVersion`, `toolInputs`, `skillId`, `skillArgs`, `skillRequiredCapabilities`, `presetKey`, and `presetInputValues`, but the product contract is the discriminated model above.

Rules:

1. `stepType` is the visible discriminator and must exist for every ordinary authored step.
2. Type-specific data is isolated by Step Type.
3. Shared fields such as title, instructions, and attachments may survive Step Type changes.
4. Incompatible type-specific fields must not be silently submitted after the user changes Step Type.
5. The UI must either clear incompatible values with visible feedback or require confirmation before discarding meaningful values.
6. Draft reconstruction for edit/rerun must preserve explicit Tool, Skill, and Preset draft states where the task snapshot contains them.
7. Legacy snapshots may be read through compatibility reconstruction, but new authoring must converge on explicit Step Type state.

---

## 7. Step editor contract

### 7.1 Step list

The step editor renders an ordered list of editable step regions.

Rules:

1. The first step is the primary step.
2. Users may add, remove, duplicate, and reorder steps where product policy permits.
3. Reordering changes authored order only; it does not create implicit dependency edges by itself.
4. The page remains valid with exactly one step region.
5. Each step owns its own Step Type, instructions, type-specific draft state, and attachments.
6. Moving a step moves its attachments and type-specific state with it.
7. Step provenance badges move with derived steps and remain metadata only.
8. A step generated by a preset may be edited like any other step after application.

### 7.2 Step Type selector

Each step exposes exactly one user-facing control named **Step Type**.

Canonical choices and helper copy:

| Step Type | Helper text |
| --- | --- |
| Tool | Run a typed integration or system operation directly. |
| Skill | Ask an agent to perform work using reusable behavior. |
| Preset | Insert a reusable set of configured steps. |

Rules:

1. The Step Type selector uses product terms: Tool, Skill, Preset.
2. The selector must not use Temporal Activity, capability, invocation, command, script, or adapter terminology as the primary discriminator.
3. Changing the selected Step Type changes the type-specific configuration controls below it.
4. The selector must be accessible as one grouped control per step.
5. Step Type helper copy must be visible or available to assistive technology.
6. Incompatible values cleared because of a Step Type change must be acknowledged with a visible notice.

### 7.3 Common step fields

Every step may expose:

1. title or generated display label;
2. instructions;
3. input attachments when policy allows;
4. source/provenance indicator when generated from a preset;
5. Advanced Settings for fields that should not be part of the common authoring path.

Rules:

1. The primary step must contain instructions or an explicit executable Tool/Skill selection.
2. Non-primary steps may omit instructions when the selected Tool/Skill/Preset provides enough typed configuration or when the step intentionally continues from prior context.
3. Step attachments are visible near the instructions or generated field they inform.
4. Generated field values are structured data, not instruction text conventions.
5. Advanced Settings must never be the only place to see invalid required configuration.

---

## 8. Tool step authoring

A Tool step runs a typed governed operation directly.

The Tool panel exposes:

1. Tool selector;
2. Tool version when applicable;
3. schema-driven input controls when a descriptor is available;
4. dynamic option providers for supported fields;
5. raw JSON input fallback in Advanced Settings;
6. visible contract metadata where useful, such as description and deterministic execution copy.

Rules:

1. Tool choices come from trusted MoonMind tool discovery when available.
2. Tool choices should be searchable and grouped by integration or domain.
3. Manual typed Tool authoring remains available when discovery is unavailable.
4. A selected Tool must submit as `type: "tool"`.
5. A Tool step must carry a Tool payload and must not carry a Skill payload.
6. Tool inputs must validate as a JSON object before submission.
7. Dynamic options must come from MoonMind APIs, not direct browser integration calls.
8. Dynamic options must not be guessed. If they cannot be loaded, the UI shows an unavailable state and preserves manual schema-shaped input where allowed.
9. Arbitrary shell, command, script, bash, or unbounded executable snippets are not a Tool step. They are rejected unless represented as an explicitly approved typed command Tool with bounded inputs and policy.
10. Tool schemas and backend validation remain authoritative.

Representative Tool payload:

```json
{
  "id": "move-jira-to-ready",
  "title": "Move Jira issue to Ready for Review",
  "type": "tool",
  "tool": {
    "id": "jira.transition_issue",
    "version": "1.0.0",
    "inputs": {
      "issueKey": "MM-123",
      "targetStatus": "Ready for Review"
    }
  }
}
```

---

## 9. Skill step authoring

A Skill step asks an agent to perform open-ended work using a selected Skill, instructions, context, runtime settings, and optional allowed Tools.

The Skill panel exposes:

1. Skill selector;
2. Skill version when applicable;
3. instructions;
4. schema-driven Skill argument controls when an input schema is available;
5. raw JSON argument fallback in Advanced Settings;
6. required capabilities or allowed tools when policy exposes them;
7. runtime compatibility hints;
8. autonomy, permissions, or context controls when the selected Skill declares them.

Rules:

1. Skill choices come from MoonMind-controlled Skill catalog or runtime-provided skill surfaces.
2. The UI may show deployment, built-in, repo, or local source provenance only when policy permits that information to be shown.
3. A selected Skill must submit as `type: "skill"`.
4. A Skill step must carry a Skill payload and must not carry a Tool payload except as explicit allowed-tool metadata for the agent.
5. Skill args must validate as a JSON object before submission.
6. Skill input schemas are optional but preferred.
7. When a Skill input schema is available, the UI renders generated controls by default.
8. Raw JSON remains available as an advanced fallback for unsupported schemas, descriptor failures, and power users.
9. Skill schemas must not be treated as a place to store secrets.
10. Runtime adapters must not broaden the selected Skill set during execution. Runtime materialization uses the resolved Skill snapshot.
11. Resolver-style Skills such as `pr-resolver` or `batch-pr-resolver`, and self-managed publish presets such as Jira Orchestrate, may enforce publish constraints. When a Skill or expanded Preset owns commit/push/merge behavior, the Create page must require or force `publishMode = "none"`, explain that constraint in the publish control or apply feedback, and must not also submit ordinary merge automation.

Representative Skill payload:

```json
{
  "id": "implement-issue",
  "title": "Implement Jira issue",
  "type": "skill",
  "instructions": "Implement MM-123 and prepare a pull request.",
  "skill": {
    "id": "code.implementation",
    "version": "1.0.0",
    "args": {
      "issueKey": "MM-123",
      "repository": "MoonLadderStudios/MoonMind"
    },
    "requiredCapabilities": ["git"]
  }
}
```

---

## 10. Preset step authoring

A Preset step configures a reusable authoring template and applies it into concrete steps.

The Preset panel exposes:

1. Preset selector;
2. Preset version when applicable;
3. preset-declared input controls;
4. preview action;
5. expansion preview;
6. warnings and validation errors;
7. apply action;
8. explicit refresh or reapply when source inputs or catalog version change.

Rules:

1. Selecting a Preset alone must not mutate executable steps.
2. Preview runs through MoonMind's preset expansion API and returns generated Tool/Skill steps, warnings, assumptions, capabilities, and provenance.
3. Applying a preview replaces the temporary Preset step with the generated executable steps.
4. Generated steps must be editable after application.
5. Generated steps must submit as flat executable Tool and/or Skill steps by default.
6. The submitted task must not contain unresolved Preset steps unless a future linked-preset execution mode is explicitly selected and visibly distinct.
7. Preset-derived steps preserve provenance when the expansion source provides it: `source.kind = "preset-derived"`, `source.presetId`, `source.presetVersion`, `source.includePath` when applicable, and `source.originalStepId` when available.
8. Provenance is metadata only. Runtime correctness depends on the generated Tool/Skill payload, not live preset lookup.
9. Refreshing from the catalog requires explicit preview and validation before replacing reviewed generated steps.
10. Preset expansion failure is non-mutating and must not corrupt unrelated draft state.

Representative Preset draft before apply:

```json
{
  "id": "jira-implementation-flow",
  "title": "Jira implementation flow",
  "type": "preset",
  "preset": {
    "id": "jira.implementation_flow",
    "version": "2026-05-02",
    "inputs": {
      "issueKey": "MM-123",
      "repository": "MoonLadderStudios/MoonMind"
    }
  }
}
```

Representative generated executable step after apply:

```json
{
  "id": "implement-issue",
  "title": "Implement Jira issue",
  "type": "skill",
  "instructions": "Implement MM-123 and prepare a pull request.",
  "skill": {
    "id": "code.implementation",
    "args": {
      "issueKey": "MM-123",
      "repository": "MoonLadderStudios/MoonMind"
    }
  },
  "source": {
    "kind": "preset-derived",
    "presetId": "jira.implementation_flow",
    "presetVersion": "2026-05-02",
    "includePath": ["root", "implementation"],
    "originalStepId": "implement"
  }
}
```

---

## 11. Schema-driven configuration system

The Create page has one generic schema-driven configuration system shared by Tool inputs, Skill args, and Preset inputs.

### 11.1 Descriptor sources

| Configured object | Descriptor source | Submitted values |
| --- | --- | --- |
| Tool | `ToolDefinition.inputs.schema` or trusted Tool discovery descriptor | `step.tool.inputs` |
| Skill | `AgentSkillDefinition.input_schema_ref`, Skill version descriptor, or runtime Skill descriptor | `step.skill.args` |
| Preset | Preset input definitions or Preset input schema | `step.preset.inputs` before apply; generated Tool/Skill inputs after apply |

Rules:

1. Descriptors are loaded through MoonMind APIs.
2. Descriptor fetch failure affects only the relevant panel.
3. The UI must keep manual JSON fallback available when safe.
4. Backend validation remains authoritative.
5. Descriptor metadata must not be used to bypass policy, authorization, or runtime compatibility checks.

### 11.2 Supported schema subset

The generic form renderer supports a practical subset of JSON Schema:

1. object schemas with `properties`;
2. `required`;
3. `title`;
4. `description`;
5. `default`;
6. `type: "string"`;
7. `type: "boolean"`;
8. `type: "integer"`;
9. `type: "number"`;
10. `enum`;
11. simple `array` fields;
12. nested object sections;
13. basic validation hints such as `minimum`, `maximum`, `minLength`, and `maxLength`.

Unsupported schema features must degrade safely: show an explanatory notice, preserve existing values, expose raw JSON editing, never silently drop user input, and never submit fields hidden because the selected Step Type changed.

### 11.3 UI schema and MoonMind widgets

Descriptors may include UI hints.

Representative hint:

```json
{
  "type": "string",
  "title": "Pull Request",
  "description": "PR number, URL, or branch.",
  "x-moonmind-widget": "github-pr-picker",
  "x-moonmind-source": {
    "repositoryFrom": "/task/repository"
  }
}
```

Supported widget categories:

| Widget | Purpose |
| --- | --- |
| `textarea` | Multi-line text. |
| `markdown` | Markdown authoring. |
| `json-object` | Structured object editor. |
| `github-repository-picker` | Select a repository from MoonMind-configured GitHub sources. |
| `github-branch-picker` | Select a branch through MoonMind GitHub branch lookup. |
| `github-pr-picker` | Select or enter a PR through MoonMind APIs. |
| `jira-project-picker` | Select a Jira project. |
| `jira-board-picker` | Select a Jira board. |
| `jira-issue-picker` | Select a Jira issue. |
| `jira-transition-picker` | Select a transition from trusted Jira transition data. |
| `repo-path-picker` | Select or enter a repository path. |
| `artifact-picker` | Select an artifact reference. |
| `secret-ref-picker` | Select or overwrite a secret reference without revealing the secret value. |

Rules:

1. Unknown widgets fall back to the base control for the field type.
2. Widgets may load dynamic options only through MoonMind APIs.
3. Widget option requests receive the relevant draft context explicitly.
4. Widget failures must be local, visible, and non-corrupting.
5. Dynamic options must not overwrite explicitly authored values without user action.
6. Dynamic pickers must preserve manual input when schema and policy allow it.

### 11.4 Defaults and value preservation

Rules:

1. Defaults are applied only when a field has no authored value.
2. Loading a new descriptor must not overwrite a field the user already edited.
3. Switching selected Tool, Skill, or Preset may offer to map compatible values, but must not silently reinterpret incompatible fields.
4. Raw JSON values and generated-form values must round-trip to the same submitted object.
5. Edit/rerun reconstruction must preserve args/inputs even if the original descriptor is no longer available.

### 11.5 Validation

Validation happens in layers:

1. frontend field-level validation for immediate feedback;
2. frontend object validation before submit;
3. backend authoritative task contract validation;
4. Tool, Skill, or Preset resolver validation before execution or expansion;
5. runtime-specific validation where applicable.

Rules:

1. Invalid JSON object text blocks submit.
2. Required generated fields block submit when empty.
3. Backend validation errors are associated with the relevant step and field where possible.
4. Schema validation must not allow hidden incompatible type-specific data to be submitted.
5. If descriptor validation and backend validation disagree, backend validation wins and the UI must show the returned error.

---

## 12. Repository, branch, publish, and merge automation

The Create page authors one branch value.

Rules:

1. The repository control accepts a configured repository option or validated `owner/repo` value.
2. The branch control is a dropdown backed by a MoonMind API.
3. The browser must never call GitHub directly.
4. Branch lookup is disabled until the repository is valid enough for lookup.
5. When the repository changes, branch options refresh for that repository.
6. If the selected branch does not exist in the new branch catalog, the authored branch value is cleared.
7. If the catalog exposes a default branch and the author has not chosen a branch, the UI may preselect the default branch.
8. `Branch` maps into the canonical publishing contract:
   - `publishMode = "none"`: selected branch maps to checkout/start branch only.
   - `publishMode = "branch"`: selected branch maps to both start and target branch for same-branch publishing.
   - `publishMode = "pr"`: selected branch maps to PR base/start branch; runtime chooses or generates the work/head branch.
9. The Create page does not author a separate target branch.
10. Cross-branch publishing remains a broader task-publishing capability, not an ordinary Create-page control.
11. Merge automation is available only for applicable `publishMode = "pr"` tasks.
12. Merge automation is hidden or disabled when the selected Skill or expanded Preset owns merge/publish behavior, such as `pr-resolver`, `batch-pr-resolver`, or Jira Orchestrate. The Publish Mode control should make the forced-None reason discoverable instead of silently removing the PR-with-merge choice.
13. Merge automation copy must explain that MoonMind waits for readiness and uses resolver behavior. It must not imply unsafe direct auto-merge.

---

## 13. Attachments

Attachments are structured inputs.

Rules:

1. Attachments are never embedded into instruction text as base64, markdown image data, HTML, or inline binary content.
2. Attachment policy comes from server-provided runtime/system configuration.
3. When attachment policy is disabled, attachment entry points are hidden.
4. The UI validates count, per-file size, total size, and allowed content types before upload.
5. Submit waits for local attachment upload to complete.
6. Upload failure remains local to the affected target.
7. The user can remove or retry failed attachments without losing unrelated draft state.
8. Attachments submitted to the task objective use `task.inputAttachments`.
9. Attachments submitted to a step use `task.steps[n].inputAttachments`.
10. Generated field controls that produce artifact refs submit those refs inside the field's configured input object, not as anonymous attachments.
11. Preview failure must not corrupt the draft.

Desired UX affordances: file picker, drag-and-drop where supported, paste-from-clipboard where supported, thumbnail preview for images, metadata display for non-previewable files, keyboard-accessible remove and retry actions, and a concise per-target attachment summary.

---

## 14. Jira integration

Jira is an external source of task inputs and dynamic configuration options.

It is not a runtime, not the primary task object, not a substitute for Step Type, and not a reason for the browser to call Jira directly.

Supported Jira-backed interactions:

1. browse projects, boards, columns, and issues;
2. import issue text into an explicit target;
3. import issue attachments into an explicit attachment target;
4. populate schema-driven fields such as issue key, board, project, or transition;
5. provide trusted dynamic options for Tool inputs such as Jira transitions.

Targets may include task objective text, task objective attachments, step instructions, step attachments, and generated schema fields for Tool, Skill, or Preset configuration.

Rules:

1. The Jira browser displays the current target.
2. Importing Jira text or attachments must have a clear user action and target.
3. Imported Jira images become structured attachments.
4. Imported Jira images are not inserted into instruction text.
5. Importing into a preset-derived step counts as a manual edit to that step.
6. Jira import failures must not mutate the draft.
7. Dynamic Jira option providers must not guess unavailable transitions, statuses, issue types, or IDs.

---

## 15. Dependencies

The dependency area is a bounded picker for existing `MoonMind.Run` executions.

Rules:

1. Users may add up to the configured dependency limit.
2. Duplicate dependencies are rejected client-side.
3. Dependency fetch failure must not block manual task creation.
4. Dependency selection is independent from attachments, Jira, schema descriptors, preset expansion, branch lookup, and provider profile lookup.
5. Dependencies are submitted as task dependencies and do not create implicit step edges inside the Create-page draft.

---

## 16. Execution context

The Create page exposes these execution-context controls:

1. runtime;
2. provider profile;
3. model;
4. effort.

Rules:

1. Runtime defaults come from server-provided configuration.
2. Provider-profile options are runtime-specific.
3. Provider-profile availability and activation state should be visible enough to prevent confusing “enabled but unusable” submissions.
4. Model and effort defaults may vary by runtime and provider profile.
5. Runtime selection may influence available Skill choices, Tool availability, attachment policy, and schema widget availability.
6. Changing runtime must not silently rewrite authored Tool/Skill/Preset values.
7. Incompatible runtime changes must surface validation messages on affected steps.

---

## 17. Edit and rerun

The Create page also serves as the edit and rerun surface for `MoonMind.Run` executions.

Rules:

1. Edit and rerun reconstruct the draft from the authoritative task input snapshot.
2. Reconstruction preserves task objective text, objective-scoped attachments, ordered steps, Step Type, Tool payloads, Skill payloads, unresolved Preset draft payloads where present, generated Tool/Skill payloads, preset provenance, step attachments, repository, branch, publish mode, merge automation settings, runtime, provider profile, model, effort, and dependencies.
3. Legacy snapshots may be reconstructed into the closest explicit Step Type.
4. Legacy Skill-shaped steps reconstruct as Skill steps unless an explicit Tool contract is present.
5. Legacy unresolved Preset-like data reconstructs as Preset draft state when the payload was authoring-only.
6. Flat executable preset-derived steps reconstruct as Tool/Skill steps with provenance badges, not as hidden live Preset steps.
7. If the descriptor for a selected Tool, Skill, or Preset is unavailable, the UI preserves submitted values and falls back to raw JSON or compact readonly metadata.
8. Rerun reuses the original task shape by default unless the user edits it.
9. Refreshing from a newer Tool, Skill, or Preset descriptor requires explicit user action.
10. If reconstruction cannot preserve required attachments or step payloads, the page must fail explicitly rather than silently dropping them.

---

## 18. Submission contract

The Create page submit flow is artifact-first and executable-step-first.

Rules:

1. Local attachments upload before create, edit, or rerun submission.
2. The browser submits structured attachment refs, not raw binary payloads.
3. The browser submits only fields relevant to the selected Step Type.
4. Unresolved Preset steps are rejected by default.
5. Applied presets submit generated executable Tool and Skill steps.
6. Preset provenance is preserved as metadata where available.
7. Runtime correctness must not require live preset lookup.
8. Oversized task input uses the artifact-backed task input fallback.
9. Submit remains explicit. Uploading attachments, selecting Jira issues, previewing presets, or loading descriptors never creates a task by itself.
10. The authoritative task input snapshot preserves enough information for edit and rerun reconstruction.

Representative task-shaped payload:

```json
{
  "type": "task",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "publishMode": "pr",
    "mergeAutomation": {
      "enabled": true
    },
    "task": {
      "instructions": "Implement MM-123 and open a PR.",
      "publish": {
        "mode": "pr"
      },
      "git": {
        "startingBranch": "main"
      },
      "steps": [
        {
          "id": "fetch-issue",
          "title": "Fetch Jira issue",
          "type": "tool",
          "tool": {
            "id": "jira.get_issue",
            "version": "1.0.0",
            "inputs": {
              "issueKey": "MM-123"
            }
          },
          "source": {
            "kind": "preset-derived",
            "presetId": "jira.implementation_flow",
            "presetVersion": "2026-05-02",
            "includePath": ["root"],
            "originalStepId": "fetch-issue"
          }
        },
        {
          "id": "implement-issue",
          "title": "Implement Jira issue",
          "type": "skill",
          "instructions": "Implement the issue and prepare a pull request.",
          "skill": {
            "id": "code.implementation",
            "args": {
              "issueKey": "MM-123",
              "repository": "MoonLadderStudios/MoonMind"
            },
            "requiredCapabilities": ["git"]
          },
          "inputAttachments": [
            {
              "artifactId": "art_step1_456",
              "filename": "design.png",
              "contentType": "image/png",
              "sizeBytes": 72109
            }
          ],
          "source": {
            "kind": "preset-derived",
            "presetId": "jira.implementation_flow",
            "presetVersion": "2026-05-02",
            "includePath": ["root", "implementation"],
            "originalStepId": "implement"
          }
        }
      ]
    }
  }
}
```

---

## 19. Objective resolution and title derivation

The task objective is the task-level summary context used for title derivation and high-level run intent.

Resolution order:

1. explicit task objective field when present;
2. preset-owned objective or initial instructions field when present in a compatible authoring surface;
3. primary step instructions;
4. most recent applied preset input that semantically aliases a feature request or task request field.

Rules:

1. Non-primary step instructions do not override the task objective.
2. Title derivation uses the first meaningful non-empty line of the resolved objective text unless the user provides an explicit title.
3. Objective-scoped attachments are part of task-level objective context.
4. Primary-step attachments may participate in task-level context because the primary step is the default authored objective.
5. Non-primary step attachments do not affect title derivation unless a Tool, Skill, or Preset explicitly promotes them through typed configuration.

---

## 20. Failure and empty-state rules

The Create page must fail visibly and locally.

Rules:

1. If attachment policy is disabled, attachment entry points are hidden and the page remains usable.
2. If repository validation fails, branch lookup must not run.
3. If branch lookup fails, the rest of the draft remains intact.
4. If a repository has no branch options, the branch control shows an explicit empty state.
5. If Tool discovery fails, manual typed Tool authoring remains available.
6. If a Tool descriptor is malformed, the Tool panel shows a descriptor error and raw JSON fallback.
7. If Skill descriptor lookup fails, the Skill panel preserves the selected Skill id and args and shows raw JSON fallback.
8. If a Skill input schema is unsupported, the Skill panel shows why generated controls are unavailable and preserves raw JSON editing.
9. If a dynamic option provider fails, existing authored values remain unchanged.
10. If a Preset preview fails, the draft is not mutated.
11. If a Preset has not been applied, submit blocks the unresolved Preset step.
12. If JSON input is invalid, submit is blocked and the specific step/field is identified.
13. If Step Type switching clears incompatible data, a notice appears near the Step Type selector.
14. If Jira is unavailable, manual authoring continues.
15. If edit/rerun cannot reconstruct required fields, the page fails explicitly rather than silently rewriting semantics.

Representative copy:

```text
Select a repository before choosing a branch.
Loading branches for owner/repo...
Failed to load branches for owner/repo. You can retry or continue with the repository default branch.
Tool discovery is unavailable. Enter a typed Tool id and JSON inputs manually.
This Skill's input form is unavailable. You can edit its args as JSON.
Preset preview failed. Your draft was not changed.
Apply this Preset before submitting. Runtime submissions cannot contain unresolved Preset steps.
Step Type changed. Incompatible Skill settings were cleared; shared instructions were preserved.
Image upload failed. Remove the image or retry before submitting.
This runtime does not currently allow image inputs.
```

---

## 21. Accessibility and interaction rules

Rules:

1. Every Step Type selector is keyboard-accessible and has an accessible name.
2. Generated schema fields use stable labels, descriptions, and error associations.
3. Required generated fields are communicated visually and to assistive technology.
4. Dynamic option loading states are perceivable.
5. Validation errors are associated with the specific step and field.
6. Attachment upload, remove, retry, preview, import, preview-preset, apply-preset, and submit actions are keyboard-accessible.
7. Focus returns predictably after Jira import, preset preview/apply, failed submit, or descriptor load failure.
8. Hidden incompatible fields are not submitted, and any clearing of meaningful data is announced.
9. Compact controls such as Branch and Publish Mode may omit visible top labels only when accessible names remain available.
10. Error summaries link to the relevant field or step region.

---

## 22. Testing requirements

The Create page test suite should cover:

1. Step Type selector renders exactly Tool, Skill, and Preset options per step.
2. Step Type helper copy uses product terminology and avoids Activity/Capability/Script as umbrella labels.
3. Switching Step Type changes visible controls.
4. Switching Step Type preserves shared instructions.
5. Switching Step Type clears or protects incompatible type-specific values and shows visible feedback.
6. Hidden Skill fields are not submitted for Tool steps.
7. Hidden Tool fields are not submitted for Skill steps.
8. Unresolved Preset steps are blocked from ordinary submission.
9. Preset preview failure is non-mutating.
10. Applying a Preset replaces the Preset placeholder with generated Tool/Skill steps.
11. Applied preset steps submit as flat executable Tool/Skill steps.
12. Preset-derived steps preserve `source.kind`, `presetId`, `presetVersion`, `includePath` when provided, and `originalStepId`.
13. Runtime materialization does not depend on preset provenance or live preset lookup.
14. Tool discovery choices are searchable and grouped when discovery is available.
15. Tool discovery failure leaves manual Tool authoring available.
16. Tool inputs validate as JSON object text before submit.
17. Dynamic Tool options such as Jira transitions load through MoonMind APIs and do not guess values.
18. Skill selection renders schema-driven args when a schema is available.
19. Skill args generated controls and raw JSON fallback round-trip to the same submitted object.
20. Skill descriptor failure preserves selected Skill id and existing args.
21. Unsupported Skill schema features degrade to JSON fallback without dropping data.
22. Required schema fields block submit with field-specific errors.
23. Branch lookup uses MoonMind APIs and never calls GitHub directly.
24. Branch selection maps to `startingBranch` / `targetBranch` according to publish mode without exposing a Target Branch control.
25. Resolver-style Skills force or require `publishMode = "none"` and disable incompatible merge automation.
26. Attachment policy hides entry points when disabled.
27. Attachment validation covers count, type, per-file size, total size, upload failure, and retry.
28. Jira import targets the selected objective, step, attachment target, or generated field only.
29. Edit reconstructs Tool, Skill, Preset, generated step, attachment, and provenance state.
30. Rerun preserves untouched attachments, Step Types, type-specific payloads, branch, publish mode, and runtime context.
31. Legacy snapshots reconstruct into explicit Step Type draft state where possible.
32. Proposal or promotion surfaces reject unresolved Preset steps and preserve flat executable payloads.
33. Explicit refresh/reapply is required before replacing reviewed preset-derived steps from the catalog.
34. TypeScript checks cover the Step Type draft model and schema-form renderer contracts.
35. Backend task-contract tests reject mixed Tool/Skill payloads, non-executable Step Types, and shell-shaped step overrides.

---

## 23. Locked decisions

1. The primary Create-page authoring model is Step Type based.
2. The canonical Step Types are Tool, Skill, and Preset.
3. Preset use belongs in the step editor.
4. A separate Preset section is management-only.
5. Normal runtime submission contains executable Tool and Skill steps only.
6. Preset provenance is metadata and does not control runtime correctness.
7. Tool inputs, Skill args, and Preset inputs use the shared schema-driven configuration system.
8. Raw JSON remains an advanced fallback.
9. Browser clients call MoonMind APIs only.
10. The Create page authors one Branch value and no Target Branch field.
11. Attachments are structured inputs and never inline binary instruction text.
12. Edit and rerun reconstruct explicit Step Type state and preserve type-specific payloads.
13. Runtime and backend validation remain authoritative over browser validation.
14. Internal runtime vocabulary must not leak into ordinary step authoring.
