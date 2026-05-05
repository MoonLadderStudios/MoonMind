# Step Types

Status: Desired-state architecture
Owners: MoonMind Engineering (Task Platform + UI)
Last Updated: 2026-05-05
Related: `docs/Tasks/TaskPresetsSystem.md`, `docs/UI/CreatePage.md`, `docs/Steps/SkillSystem.md`, `docs/Steps/JiraIntegration.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Tools/DockerComposeUpdateSystem.md`

---

## 1. Purpose

Define the desired-state MoonMind **Step Type** model.

A MoonMind task is composed from steps. Each step has exactly one user-facing Step Type that determines:

1. what the step represents,
2. which capability selector is shown,
3. which schema-driven input form is rendered,
4. how the step is validated,
5. whether the step is executable as-authored or expands into executable steps,
6. how the step maps into the runtime plan and Temporal execution model.

The Create page uses Step Type as the main authoring discriminator. Users should not need to understand internal terms such as capability registries, Temporal activities, runtime adapter commands, plan nodes, or worker placement when authoring ordinary tasks.

---

## 2. Desired-State Summary

The canonical normalized Step Types are:

1. `tool`
2. `skill`
3. `preset`

```text
Step Type
[ Tool ] [ Skill ] [ Preset ]
```

Changing the Step Type changes the selector and schema-driven form below it.

```text
Tool   -> choose a typed operation and configure its inputs
Skill  -> choose agent behavior and configure its inputs/runtime context
Preset -> choose a reusable step composition and configure its preset inputs
```

A Tool step and a Skill step are executable step types.

A Preset step is an authoring-time composition step. It may remain configured but unexpanded in the draft and even in the Create Task submission request. The backend submit path must validate and expand all unresolved Preset steps before creating the executable workflow. No runtime workflow should execute an unresolved Preset step by catalog lookup unless a future linked-preset execution mode is explicitly introduced.

Product surfaces may expose friendly shortcuts such as **Instructions**, **Managed Agent**, or **External Agent**. Those shortcuts must normalize into the canonical model, usually as Skill steps with default capability/runtime selections. They are not separate canonical Step Types unless this document is explicitly revised.

---

## 3. Terminology

| Term | Desired meaning |
|------|-----------------|
| **Task** | A top-level user request submitted to MoonMind. |
| **Step** | A user-visible unit of work inside a task or draft plan. |
| **Step Type** | The user-facing discriminator for how a step is configured and materialized. Canonical normalized values: `tool`, `skill`, `preset`. |
| **Capability** | A selectable catalog item behind a step, such as a tool definition, skill definition, or preset definition. Capability is acceptable as an internal/catalog term, not as the primary user-facing Step Type label. |
| **Tool** | A typed, schema-backed, policy-checked operation MoonMind can run directly. Examples: transition a Jira issue, create a pull request, update a deployment stack. |
| **Skill** | Agent-facing reusable behavior, instructions, or execution mode used when the step requires reasoning, implementation, planning, synthesis, or other open-ended work. |
| **Preset** | A reusable, parameterized authoring composition that expands into one or more concrete steps. |
| **Input Schema** | A JSON Schema-compatible contract describing which values a selected capability expects. |
| **UI Schema** | Optional presentation metadata used by the Create page schema-form renderer. |
| **Expansion** | The deterministic backend-owned process of turning a preset plus validated inputs into concrete steps. |
| **Provenance** | Metadata recording which preset or catalog item produced a step and which input snapshot influenced it. |
| **Plan** | The runtime execution artifact derived from a task's executable steps. |
| **Activity** | A Temporal implementation detail for side-effecting work. It is not a user-facing Step Type. |

The term **Capability** should not be used as the umbrella product label in the step picker. The product-facing label is **Step Type**.

---

## 4. Core Invariants

1. Every authored step has exactly one Step Type.
2. The Step Type controls the available sub-options for that step.
3. The selected capability supplies `input_schema` / `inputSchema`, optional `ui_schema` / `uiSchema`, defaults, and validation metadata.
4. The Create page renders type-specific inputs from schemas and a reusable widget registry.
5. The Create page must not hard-code preset-specific or skill-specific forms.
6. `tool` and `skill` steps are executable.
7. `preset` steps are authoring-time composition steps that expand into executable steps before runtime execution.
8. Drafts may contain unresolved Preset steps.
9. The Create Task submit path may accept unresolved Preset steps only because it expands them before workflow creation.
10. Runtime workflows must not depend on live preset catalog lookup for unresolved preset execution by default.
11. Preset expansion must be deterministic and validated before execution.
12. Preset provenance is audit and reconstruction metadata, not hidden runtime work.
13. Arbitrary shell snippets are not a Step Type.
14. Temporal Activity is not a Step Type.
15. Legacy payload shapes may be read during migration, but new authoring surfaces should converge on the Step Type model.

---

## 5. Shared Capability Input Contract

Each selectable capability should expose a normalized input contract to the Create page.

```json
{
  "id": "jira-orchestrate",
  "kind": "preset",
  "label": "Jira Orchestrate",
  "description": "Build and execute a workflow from a Jira issue.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {}
}
```

The contract applies to tools, skills, and presets.

The Create page uses the same schema-form renderer regardless of whether the selected capability is a Tool, Skill, or Preset. The renderer supports standard JSON Schema concepts plus optional MoonMind UI hints:

- `type`
- `title`
- `description`
- `default`
- `required`
- `properties`
- `items`
- `enum`
- `oneOf` / `anyOf` when needed
- `format`
- `x-moonmind-*` extension fields
- optional `uiSchema` widget metadata

This contract should remain compatible with the direction of MoonMind skill input schemas and Agent Skills-style manifests: capabilities declare typed inputs in metadata, and UI/orchestration layers consume those declarations without custom code for every capability.

---

## 6. Step Type Taxonomy

### 6.1 `tool`

A Tool step runs a typed executable operation.

Use a Tool step when the desired work is explicit, bounded, and can be represented as a known operation with typed inputs and outputs.

Examples:

1. Fetch a Jira issue.
2. Transition a Jira issue.
3. Add a Jira comment.
4. Create a GitHub pull request.
5. Request GitHub reviewers.
6. Run a test command through a controlled runner.
7. Update a Docker Compose deployment stack through a privileged typed contract.

A Tool is not an arbitrary script. Tool definitions must declare their contract:

1. name and version,
2. input schema,
3. output schema,
4. required authorization,
5. required worker capabilities,
6. retry policy,
7. execution binding,
8. validation and error model.

Example UI:

```text
Step Type: Tool
Tool: Jira -> Transition Issue
Issue key: MM-123
Target status: In Progress
Comment: optional
```

Desired payload shape:

```json
{
  "id": "move-jira-to-in-progress",
  "title": "Move Jira issue to In Progress",
  "type": "tool",
  "tool": {
    "id": "jira.transition_issue",
    "version": "1.0.0",
    "inputs": {
      "issueKey": "MM-123",
      "targetStatus": "In Progress"
    }
  }
}
```

### 6.2 `skill`

A Skill step invokes agent-facing behavior.

Use a Skill step when the desired work requires interpretation, planning, implementation, synthesis, troubleshooting, or other open-ended reasoning.

Examples:

1. Implement a Jira issue in a repository.
2. Triage an ambiguous Jira issue.
3. Convert a feature request into a MoonSpec.
4. Resolve a pull request review thread.
5. Investigate failing tests and propose a fix.
6. Run a managed agent runtime such as Codex CLI, Claude Code, or Gemini CLI with a selected behavior profile.
7. Delegate to an external agent provider through a supported integration.

A Skill step may use tools internally, but the user-authored step is still a Skill because the primary work is agentic.

A Skill step exposes a selected skill/capability and schema-driven inputs such as:

1. instructions,
2. repository or project context,
3. Jira issue or artifact context,
4. runtime or provider profile preferences,
5. model override when allowed,
6. autonomy/approval controls,
7. allowed tools or required permissions.

Example UI:

```text
Step Type: Skill
Skill: Code Implementation
Repository: MoonLadderStudios/MoonMind
Instructions: Implement MM-123 and open a PR.
```

Desired payload shape:

```json
{
  "id": "implement-issue",
  "title": "Implement Jira issue",
  "type": "skill",
  "skill": {
    "id": "code.implementation",
    "version": "1.0.0",
    "inputs": {
      "repository": "MoonLadderStudios/MoonMind",
      "issueKey": "MM-123",
      "instructions": "Implement the issue and prepare a pull request."
    }
  }
}
```

#### Instructions shortcut

The Create page may offer an **Instructions** shortcut for a plain natural-language step. This should normalize to a Skill step with a default skill/capability such as `agent.instructions` or equivalent. It should not create a fourth canonical Step Type unless this document is revised.

#### Managed and external agent shortcuts

The Create page may expose **Managed Agent** or **External Agent** shortcuts if that improves usability. Those shortcuts should normalize to Skill steps with runtime/provider-specific inputs. Runtime choice is configuration, not the Step Type itself.

### 6.3 `preset`

A Preset step selects a reusable composition and configures its inputs.

Use a Preset step when the user wants to insert or submit a known workflow shape rather than configure each step manually.

Examples:

1. Jira implementation flow.
2. Jira breakdown flow.
3. MoonSpec orchestration flow.
4. PR review and fix flow.
5. Deployment verification flow.
6. PR with merge automation flow.

A Preset step is not directly executable by default. It is a configured composition request. It may be previewed, applied into editable child steps, or submitted unexpanded so the backend submit path expands it before workflow creation.

Example UI:

```text
Step Type: Preset
Preset: Jira Orchestrate
Jira issue: MM-123 — Add schema-driven preset inputs

[Preview expansion]
[Apply preset]
```

Temporary draft payload before expansion:

```json
{
  "id": "apply-jira-orchestrate",
  "title": "Jira Orchestrate",
  "type": "preset",
  "preset": {
    "id": "jira-orchestrate",
    "version": "1",
    "inputs": {
      "jira_issue": {
        "key": "MM-123",
        "summary": "Add schema-driven preset inputs"
      }
    }
  },
  "expansionState": "not_expanded"
}
```

After preview, apply, or submit-time expansion, generated steps should include provenance:

```json
{
  "id": "implement-issue",
  "title": "Implement MM-123",
  "type": "skill",
  "skill": {
    "id": "code.implementation",
    "version": "1.0.0",
    "inputs": {
      "repository": "MoonLadderStudios/MoonMind",
      "jira_issue_key": "MM-123"
    }
  },
  "provenance": {
    "sourceType": "preset",
    "presetId": "jira-orchestrate",
    "presetVersion": "1",
    "inputSnapshot": {
      "jira_issue": {
        "key": "MM-123"
      }
    }
  }
}
```

---

## 7. Schema-Driven Step Editor UX

The step editor renders controls from the selected Step Type and selected capability.

```text
Step
  Title
  Step Type
  Capability selector
  Schema-generated inputs
  Advanced options
```

When the user changes Step Type, the UI must either:

1. preserve compatible fields,
2. clearly discard incompatible fields, or
3. require confirmation when meaningful data would be lost.

### 7.1 Step type picker

The Step Type picker should use concise labels and explanatory helper text:

| Step Type | Helper text |
|-----------|-------------|
| Tool | Run a typed integration or system operation directly. |
| Skill | Ask an agent to perform work using reusable behavior. |
| Preset | Configure a reusable workflow shape that can expand into steps. |

### 7.2 Capability picker

Each Step Type has a capability picker:

- Tool picker lists typed operations grouped by integration/domain.
- Skill picker lists reusable agent behaviors, instructions, and runtime-compatible skills.
- Preset picker lists reusable workflow compositions.

Selecting a capability loads its input contract and renders schema-driven fields.

### 7.3 Generic widget registry

The schema-form renderer uses a local widget registry. Widgets are reusable field components, not workflow-specific forms.

Examples:

| Widget | Use |
| --- | --- |
| `text` | single-line string input |
| `textarea` | multi-line string input |
| `number` | numeric input |
| `checkbox` | boolean input |
| `select` | enum / one-of selector |
| `multi-select` | array of enum values |
| `json` | advanced object editor fallback |
| `jira.issue-picker` | Jira issue lookup and selection |
| `github.branch-picker` | branch lookup and selection |
| `provider.profile-picker` | provider profile selection |
| `model-picker` | model selection constrained by provider/runtime |
| `file-reference-picker` | uploaded file or artifact reference selection |

Only widgets are allowed to have custom UI components. The page must not have branches for individual preset IDs, skill IDs, or tool IDs.

---

## 8. Preset Input and Expansion Contract

Presets must declare their expected inputs through `input_schema` / `inputSchema` and optional `ui_schema` / `uiSchema`.

Example preset input contract:

```yaml
inputSchema:
  type: object
  required:
    - jira_issue
  properties:
    jira_issue:
      type: object
      title: Jira issue
      required:
        - key
      properties:
        key:
          type: string
        summary:
          type: string
        description:
          type: string
        url:
          type: string
          format: uri

uiSchema:
  jira_issue:
    widget: jira.issue-picker
    searchPlaceholder: Search Jira issues
    allowManualKeyEntry: true
```

The Create page renders the Jira issue picker because the schema requests `jira.issue-picker`, not because the preset ID is known to the page.

### 8.1 Preview

Preview calls the backend expansion service and shows generated steps without replacing the Preset step.

### 8.2 Apply

Apply calls the backend expansion service and inserts generated child steps into the draft. Applied steps remain editable and retain provenance.

### 8.3 Reapply

Reapply regenerates steps from the saved preset ID, preset version, and current inputs. If generated child steps were edited, the UI must explain whether reapply replaces, merges, or appends regenerated steps.

### 8.4 Submit-time auto-expansion

The user may submit a task while Preset steps remain unexpanded. The submit path must:

1. validate all non-preset fields,
2. validate each Preset step's inputs against its schema,
3. expand all unexpanded Preset steps through the backend expansion service,
4. recursively expand nested presets,
5. validate the final concrete step list,
6. create the executable workflow.

If expansion fails, the Create page displays field-addressable errors and preserves the user's entered values.

---

## 9. Runtime and Payload Contract

### 9.1 Draft payload

Draft task authoring may contain any canonical Step Type:

1. `type: "tool"`
2. `type: "skill"`
3. `type: "preset"`

### 9.2 Create Task submission payload

The Create Task submission endpoint may accept unresolved Preset steps, but only as an authoring convenience. The backend must expand them before workflow creation.

### 9.3 Runtime payload

Runtime execution should contain only executable steps by default:

1. `type: "tool"`
2. `type: "skill"`

Preset-derived runtime steps carry provenance metadata, but they do not depend on the preset catalog for runtime correctness.

### 9.4 Runtime plan mapping

Desired mapping:

| Step Type | Runtime materialization |
|-----------|-------------------------|
| `tool` | Plan node invoking a typed tool definition. |
| `skill` | Plan node, child workflow, activity, or managed session request invoking agent-facing behavior. |
| `preset` | No runtime node by default; expands before workflow creation. |

The execution layer may translate Tool steps into Temporal Activities and Skill steps into activities, child workflows, or runtime-specific managed sessions. That translation is an implementation concern and should not affect the Step Type UI.

---

## 10. Validation Rules

### 10.1 Common validation

Every step must have:

1. stable local identity,
2. title or generated display label,
3. Step Type,
4. type-specific payload,
5. schema-valid inputs,
6. validation errors surfaced before submission.

### 10.2 Tool validation

A Tool step is valid only when:

1. the selected tool exists,
2. the tool version can be resolved or pinned,
3. inputs validate against the tool schema,
4. the user has required authorization,
5. required worker capabilities are available,
6. forbidden fields are absent,
7. retry and side-effect policy is known.

Tool validation must reject arbitrary shell snippets unless the selected tool is an explicitly approved typed command tool with bounded inputs and policy.

### 10.3 Skill validation

A Skill step is valid only when:

1. the selected skill exists or can be resolved by documented `auto` semantics,
2. skill inputs validate against the skill contract,
3. runtime compatibility is known,
4. required context is present,
5. selected tools or permissions are allowed,
6. approval/autonomy constraints are enforceable.

### 10.4 Preset validation

A Preset step is valid for preview, apply, or submit-time expansion only when:

1. the preset exists,
2. the preset version is active or explicitly previewable,
3. inputs validate against the preset input schema,
4. expansion succeeds deterministically,
5. generated steps validate under their own Tool or Skill rules,
6. step count and policy limits are enforced,
7. expansion warnings are visible to the user.

Validation errors must be field-addressable:

```json
{
  "path": "steps[0].inputs.jira_issue.key",
  "message": "A Jira issue is required.",
  "code": "required"
}
```

---

## 11. Jira Example

Jira interactions illustrate why Step Type matters.

Some Jira work is deterministic and should be a Tool step:

```text
Step Type: Tool
Tool: Jira -> Transition Issue
Issue key: MM-123
Target status: Ready for Review
```

Other Jira work is agentic and should be a Skill step:

```text
Step Type: Skill
Skill: Jira Triage
Instructions: Read the issue and decide whether it needs clarification, breakdown, or implementation.
```

A reusable Jira workflow should be a Preset step while authoring:

```text
Step Type: Preset
Preset: Jira Orchestrate
Jira issue: MM-123
```

The Jira issue field is generated from the preset schema:

```yaml
uiSchema:
  jira_issue:
    widget: jira.issue-picker
```

Expanding the preset may produce both Tool and Skill steps:

1. Tool: fetch Jira issue,
2. Tool: transition Jira issue to In Progress,
3. Skill: implement issue,
4. Tool: run tests,
5. Tool: create pull request,
6. Tool: add Jira comment,
7. Tool: transition Jira issue to Ready for Review.

This keeps simple Jira state changes deterministic while still supporting agentic work when interpretation or implementation is required.

---

## 12. Naming Policy

### 12.1 Keep `Tool`

MoonMind should keep **Tool** as the user-facing Step Type for typed executable operations.

Preferred terms:

1. Tool,
2. Typed Tool,
3. Executable Tool,
4. Tool Definition.

Avoid using **Script** as the canonical Step Type. Script implies arbitrary code or shell execution and weakens the desired distinction between typed, governed operations and ad hoc commands. A controlled script runner may exist as a Tool if it has a typed contract and policy controls.

Avoid using **Executable** as the main UI label. It is accurate as an adjective but awkward as a Step Type and can imply binaries rather than typed product operations.

### 12.2 Use `Step Type` in UI

Preferred UI label:

```text
Step Type
```

Avoid as the primary user-facing discriminator:

1. Capability,
2. Activity,
3. Invocation,
4. Command,
5. Script.

These terms may still appear in narrow technical contexts, but they should not replace Step Type in the authoring UI.

### 12.3 Keep `Activity` Temporal-specific

Activity means Temporal Activity. It should remain an implementation concept in Temporal design docs and worker execution code.

Do not use Activity as the Step Type label.

---

## 13. API Shape

The desired API shape is explicit and discriminated.

```ts
type StepType = "tool" | "skill" | "preset";

type StepProvenance = {
  sourceType: "preset" | "proposal" | "manual";
  presetId?: string;
  presetVersion?: string;
  inputSnapshot?: Record<string, unknown>;
  parentPresetPath?: string[];
};

type BaseStep = {
  id: string;
  title?: string;
  type: StepType;
  provenance?: StepProvenance;
};

type ToolStep = BaseStep & {
  type: "tool";
  tool: {
    id: string;
    version?: string;
    inputs: Record<string, unknown>;
  };
};

type SkillStep = BaseStep & {
  type: "skill";
  skill: {
    id: string;
    version?: string;
    inputs: Record<string, unknown>;
  };
};

type PresetStep = BaseStep & {
  type: "preset";
  preset: {
    id: string;
    version?: string;
    inputs: Record<string, unknown>;
  };
  expansionState?: "not_expanded" | "previewed" | "applied" | "error";
};

type DraftStep = ToolStep | SkillStep | PresetStep;
type ExecutableStep = ToolStep | SkillStep;
```

Preset expansion APIs accept `PresetStep` and return concrete executable steps plus provenance metadata.

```ts
type ExpandPresetRequest = {
  presetId: string;
  presetVersion?: string;
  inputs: Record<string, unknown>;
  context: Record<string, unknown>;
};

type ExpandPresetResponse = {
  steps: ExecutableStep[];
  warnings: ValidationWarning[];
};
```

---

## 14. Preset Management vs Preset Use

Preset management and preset use are separate experiences.

Preset management lives in the Presets section:

1. catalog browsing,
2. create/edit/version,
3. governance and lifecycle,
4. save-from-task,
5. audit and usage inspection,
6. expansion testing.

Preset use lives inside step authoring:

1. add or edit a step,
2. choose `Step Type = Preset`,
3. select a preset,
4. configure schema-generated inputs,
5. preview generated steps,
6. apply into executable steps or submit unexpanded for backend auto-expansion.

There should not be a separate Presets section for choosing and applying a preset to the current task. The Presets section is management-only.

---

## 15. Proposal and Promotion Semantics

Task proposals must preserve executable intent.

When a proposal is created from preset-derived work, it may carry preset provenance, but the stored promotable task payload should be executable and flattened by default unless the proposal is explicitly still in draft-authoring form.

Promotion must not silently re-expand a live preset catalog entry. If the user wants to refresh a proposal or draft to the latest preset version, that must be an explicit action with preview and validation.

Rules:

1. Stored executable proposals should contain Tool and Skill steps.
2. Draft proposals may contain Preset steps only when they will go through the same submit-time expansion path.
3. Preset provenance may be preserved as metadata.
4. Promotion validates the reviewed payload.
5. Promotion does not require live preset lookup for runtime correctness after expansion.
6. Refreshing from a preset catalog is explicit, not automatic.

---

## 16. Migration Guidance

The implementation may migrate in phases.

### Phase 1: UI terminology

1. Rename the step selector label to **Step Type**.
2. Offer Tool, Skill, and Preset in the same step editor.
3. Keep existing backend fields where necessary.
4. Normalize UI copy so Tool means typed operation, not arbitrary script.
5. Represent managed/external agent shortcuts as Skill defaults rather than new canonical Step Types.

### Phase 2: Schema-driven forms

1. Normalize tool, skill, and preset catalog entries to expose `inputSchema`, `uiSchema`, and defaults.
2. Add a shared schema-form renderer.
3. Add reusable widget registry support.
4. Remove preset-specific Create page branches.
5. Prove a new preset can add inputs without Create page code changes.

### Phase 3: Draft model normalization

1. Introduce explicit `step.type` in draft state.
2. Model Tool, Skill, and Preset sub-payloads separately.
3. Preserve compatibility with existing `skillId`, `tool`, and template fields.
4. Add validation that rejects invalid mixed-type steps.

### Phase 4: Preset expansion normalization

1. Make Preset a configured composition step.
2. Support preview, apply, reapply, and submit-time expansion through the same backend service.
3. Preserve provenance on expanded steps.
4. Ensure runtime payloads are executable without live preset lookup.

### Phase 5: Runtime contract convergence

1. Compile executable steps into the canonical plan format.
2. Pin tool and skill registry snapshots where required.
3. Align proposal promotion, task editing, and execution reconstruction with the Step Type model.

---

## 17. Non-Goals

1. Redefining Temporal Activity semantics.
2. Replacing the plan executor.
3. Making presets hidden runtime work.
4. Introducing arbitrary shell scripts as a first-class Step Type.
5. Removing legacy compatibility readers immediately.
6. Requiring users to understand worker capability placement to author ordinary steps.
7. Treating Tool and Skill as the same thing merely because both may eventually map into plan nodes.
8. Adding a custom Create page form for each preset.

---

## 18. Open Design Decisions

### Q1: Should linked presets exist?

Desired default: no. Presets expand into concrete steps before runtime execution.

A future linked-preset mode may exist, but it must be explicit and visibly different from ordinary preset application. It would need separate rules for version pinning, drift detection, refresh behavior, validation, audit, and runtime lookup.

### Q2: Should the API use `step.type` or `step.action.kind`?

Preferred user-facing term: `Step Type`.

Preferred desired-state payload in this document: `step.type`.

`step.action.kind` remains a reasonable internal alternative if implementation constraints require nesting execution configuration under `action`. The UI should still say Step Type.

### Q3: Should `tool` be renamed to `script` or `executable`?

Desired answer: no.

Keep `tool` as the user-facing Step Type and define it as a typed executable operation. Use `Typed Tool` or `Executable Tool` in technical docs when extra precision is needed. Avoid `Script` for governed operations.

### Q4: Should Instructions be its own canonical Step Type?

Desired default: no.

Instructions should be a friendly Create page shortcut that normalizes to a Skill step with a default agent behavior. A separate `instructions` Step Type would add complexity without changing execution semantics.
