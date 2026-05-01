# Step Types

Status: Draft
Owners: MoonMind Engineering (Task Platform + UI)
Last Updated: 2026-04-28
Related: `docs/Tasks/TaskPresetsSystem.md`, `docs/Tasks/SkillAndPlanContracts.md`, `docs/Tasks/AgentSkillSystem.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Tools/DockerComposeUpdateSystem.md`

---

## 1. Purpose

Define the desired-state MoonMind **Step Type** model.

A MoonMind task is composed from steps. Each step has exactly one user-facing
**Step Type** that determines:

1. what the step represents,
2. which configuration fields are shown,
3. how the step is validated,
4. whether the step is executable as-authored or expands into executable steps,
5. how the step maps into the runtime plan and Temporal execution model.

The canonical Step Types are:

1. `tool`
2. `skill`
3. `preset`

The product-facing label is **Step Type**. Users should not need to understand
internal terms such as capability registries, Temporal activities, plan nodes, or
runtime adapter commands when authoring ordinary tasks.

---

## 2. Desired-State Summary

MoonMind should expose one step-authoring control:

```text
Step Type
[ Tool ] [ Skill ] [ Preset ]
```

Changing the Step Type changes the form below it.

```text
Tool   -> choose a typed operation and configure its inputs
Skill  -> choose agent behavior and configure its instructions/runtime context
Preset -> choose a reusable template, configure preset inputs, expand
```

A **Tool** step and a **Skill** step are executable step types.

A **Preset** step is normally an authoring-time placeholder. Applying a preset
expands it into concrete Tool and/or Skill steps. The durable execution payload
should contain expanded executable steps, not unresolved preset invocations,
unless a future linked-preset mode is explicitly introduced.

---

## 3. Terminology

| Term | Desired meaning |
|------|-----------------|
| **Task** | A top-level user request submitted to MoonMind. |
| **Step** | A user-visible unit of work inside a task or plan. |
| **Step Type** | The user-facing discriminator for how a step is configured and materialized. Canonical values: `tool`, `skill`, `preset`. |
| **Tool** | A typed, schema-backed, policy-checked operation MoonMind can run directly. Examples: transition a Jira issue, add a Jira comment, create a pull request, update a deployment stack. |
| **Skill** | Agent-facing reusable behavior, instructions, or execution mode used when the step requires reasoning, implementation, planning, or open-ended work. |
| **Preset** | A reusable, parameterized authoring template that expands into one or more concrete steps. |
| **Expansion** | The deterministic process of turning a preset plus user inputs into concrete steps. |
| **Plan** | The runtime execution artifact derived from a task's executable steps. |
| **Activity** | A Temporal implementation detail for side-effecting work. It is not the user-facing Step Type label. |

The term **Capability** should not be used as the umbrella product term for Tool,
Skill, and Preset. It may still appear in security or worker-placement contexts,
such as `requiredCapabilities`, where it means a required permission or worker
affordance.

---

## 4. Core Invariants

1. Every authored step has exactly one Step Type.
2. The Step Type controls the available sub-options for that step.
3. `tool` and `skill` steps are executable.
4. `preset` steps are authoring-time placeholders by default.
5. Applying a preset produces concrete executable steps.
6. Preset expansion must be deterministic and validated before execution.
7. Preset-derived execution must not depend on live catalog lookup at runtime.
8. Preset provenance is audit and reconstruction metadata, not hidden runtime work.
9. Arbitrary shell snippets are not a Step Type.
10. Temporal Activity is not a Step Type.
11. Existing legacy payload shapes may be read during migration, but new authoring
    surfaces should converge on the Step Type model.
12. Step Type terminology must stay consistent across UI, API, docs, validation,
    proposal promotion, and preset expansion.

---

## 5. Step Type Taxonomy

### 5.1 `tool`

A Tool step runs a typed executable operation.

Use a Tool step when the desired work is explicit, bounded, and can be represented
as a known operation with typed inputs and outputs.

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

A Tool step should be presented in the UI as direct, deterministic work:

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

### 5.2 `skill`

A Skill step invokes agent-facing behavior.

Use a Skill step when the desired work requires interpretation, planning,
implementation, synthesis, or other open-ended reasoning.

Examples:

1. Implement a Jira issue in a repository.
2. Triage an ambiguous Jira issue.
3. Convert a feature request into a Moon Spec.
4. Resolve a pull request review thread.
5. Investigate failing tests and propose a fix.

A Skill step may use tools internally, but the user-authored step is still a
Skill because the primary work is agentic.

A Skill step should expose fields such as:

1. skill selector,
2. instructions,
3. relevant repository or project context,
4. runtime or model preferences when applicable,
5. allowed tools or required capabilities when applicable,
6. approval or autonomy controls when applicable.

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

### 5.3 `preset`

A Preset step selects a reusable template and configures its inputs.

Use a Preset step when the user wants to insert a known multi-step workflow rather
than configure each step manually.

Examples:

1. Jira implementation flow.
2. Jira breakdown flow.
3. Moon Spec orchestration flow.
4. PR review and fix flow.
5. Deployment verification flow.

A Preset step is not normally executable. It is a temporary authoring state that
supports preview and application.

Example UI:

```text
Step Type: Preset
Preset: Jira -> Implement Issue with PR
Issue key: MM-123
Start status: In Progress
Success status: Ready for Review
Implementation skill: Code Implementation

[Preview expansion]
[Apply preset]
```

Temporary authoring payload before expansion:

```json
{
  "id": "apply-jira-implementation-flow",
  "title": "Apply Jira implementation flow",
  "type": "preset",
  "preset": {
    "id": "jira.implementation_flow",
    "version": "2026-04-28",
    "inputs": {
      "issueKey": "MM-123",
      "startStatus": "In Progress",
      "successStatus": "Ready for Review",
      "implementationSkill": "code.implementation"
    }
  }
}
```

After application, the draft should contain executable Tool and Skill steps:

```json
[
  {
    "id": "fetch-jira-issue",
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
      "presetVersion": "2026-04-28",
      "originalStepId": "fetch-issue"
    }
  },
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
    },
    "source": {
      "kind": "preset-derived",
      "presetId": "jira.implementation_flow",
      "presetVersion": "2026-04-28",
      "originalStepId": "start-work"
    }
  },
  {
    "id": "implement-issue",
    "title": "Implement Jira issue",
    "type": "skill",
    "skill": {
      "id": "code.implementation",
      "version": "1.0.0",
      "inputs": {
        "repository": "MoonLadderStudios/MoonMind",
        "issueKey": "MM-123"
      }
    },
    "source": {
      "kind": "preset-derived",
      "presetId": "jira.implementation_flow",
      "presetVersion": "2026-04-28",
      "originalStepId": "implement"
    }
  }
]
```

---

## 6. User Experience Contract

### 6.1 Step editor

The step editor must render controls from the selected Step Type.

```text
Step
  Title
  Step Type
  Type-specific configuration
  Advanced options
```

When the user changes Step Type, the UI must either:

1. preserve compatible fields,
2. clearly discard incompatible fields, or
3. require confirmation when meaningful data would be lost.

### 6.2 Step type picker

The Step Type picker should use concise labels and explanatory helper text:

| Step Type | Helper text |
|-----------|-------------|
| Tool | Run a typed integration or system operation directly. |
| Skill | Ask an agent to perform work using reusable behavior. |
| Preset | Insert a reusable set of configured steps. |

### 6.3 Tool picker

Tool selection should support search and grouping by integration or domain.

Examples:

```text
Jira
  Fetch issue
  Transition issue
  Add comment
  Assign issue

GitHub
  Create pull request
  Request reviewers
  Add labels

Deployment
  Update Compose stack
```

Each tool form should be schema-driven. Dynamic options should be supported
through option providers. For example, a Jira transition tool can derive target
statuses from the selected issue and the current user's permissions.

### 6.4 Skill picker

Skill selection should support search, descriptions, and compatibility hints.

Skill configuration should make the agentic boundary clear. Users should be able
to distinguish deterministic Tool steps from agentic Skill steps.

### 6.5 Preset picker

Presets should be selectable from the same step-authoring surface as Tool and
Skill. There should not be a separate Presets section for using or applying a
preset.

The Presets section is for management only:

1. create preset,
2. edit preset,
3. version preset,
4. duplicate preset,
5. deprecate preset,
6. test preset expansion,
7. inspect audit metadata.

Using a preset belongs in the step editor.

### 6.6 Preset expansion

Expanding a preset replaces the temporary Preset step with generated executable
steps such as:

```text
This preset will insert 7 steps:
1. Fetch Jira issue
2. Move Jira issue to In Progress
3. Implement issue
4. Run tests
5. Create pull request
6. Comment on Jira
7. Move Jira issue to Ready for Review
```

The user can then edit those steps like ordinary steps.

The UI should support:

1. undo expansion,
2. show preset origin,
3. detach from preset provenance,
4. compare generated steps with source preset when possible,
5. update to a newer preset version only as an explicit user action.

---

## 7. Runtime and Payload Contract

### 7.1 Authoring payload

Draft task authoring may temporarily contain `type: "preset"` while the user is
configuring a preset before expansion.

Executable task submission should contain only executable steps by default:

1. `type: "tool"`
2. `type: "skill"`

Preset-derived steps should preserve source metadata:

```json
{
  "source": {
    "kind": "preset-derived",
    "presetId": "jira.implementation_flow",
    "presetVersion": "2026-04-28",
    "includePath": ["root", "implementation"],
    "originalStepId": "implement"
  }
}
```

This metadata is for audit, UI grouping, proposal reconstruction, and review. It
must not be required for runtime correctness.

### 7.2 Runtime plan mapping

Executable steps compile into the runtime plan contract.

Desired mapping:

| Step Type | Runtime materialization |
|-----------|-------------------------|
| `tool` | Plan node invoking a typed tool definition. |
| `skill` | Plan node or agent execution request invoking agent-facing skill behavior. |
| `preset` | No runtime node by default; expands before submission. |

The execution layer may translate Tool steps into Temporal Activities and Skill
steps into activities, child workflows, or runtime-specific managed sessions. That
translation is an implementation concern and should not affect the Step Type UI.

### 7.3 Backward compatibility

During migration, MoonMind may continue reading legacy shapes such as:

1. `step.skill`,
2. `step.tool`,
3. `step.skillId`,
4. preset/template step metadata,
5. plan nodes with existing `tool.type` values.

New authoring surfaces should still normalize into the desired Step Type model.
Compatibility readers must not cause new UI or docs to reintroduce ambiguous
umbrella terminology.

---

## 8. Validation Rules

### 8.1 Common validation

Every step must have:

1. stable local identity,
2. title or generated display label,
3. Step Type,
4. type-specific payload,
5. validation errors surfaced before submission.

### 8.2 Tool validation

A Tool step is valid only when:

1. the selected tool exists,
2. the tool version can be resolved or pinned,
3. inputs validate against the tool schema,
4. the user has required authorization,
5. required worker capabilities are available,
6. forbidden fields are absent,
7. retry and side-effect policy is known.

Tool validation must reject arbitrary shell snippets unless the selected tool is
an explicitly approved typed command tool with bounded inputs and policy.

### 8.3 Skill validation

A Skill step is valid only when:

1. the selected skill exists or can be resolved by documented `auto` semantics,
2. skill inputs validate against the skill contract,
3. runtime compatibility is known,
4. required context is present,
5. selected tools or permissions are allowed,
6. approval/autonomy constraints are enforceable.

### 8.4 Preset validation

A Preset step is valid for preview/application only when:

1. the preset exists,
2. the preset version is active or explicitly previewable,
3. inputs validate against the preset input schema,
4. expansion succeeds deterministically,
5. generated steps validate under their own Tool or Skill rules,
6. step count and policy limits are enforced,
7. expansion warnings are visible to the user.

A submitted task must not contain unresolved Preset steps unless the submit path
explicitly supports a future linked-preset execution mode.

---

## 9. Jira Example

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
Preset: Jira Implementation Flow
Issue key: MM-123
```

Applying the preset may expand into both Tool and Skill steps:

1. Tool: fetch Jira issue,
2. Tool: transition Jira issue to In Progress,
3. Skill: implement issue,
4. Tool: run tests,
5. Tool: create pull request,
6. Tool: add Jira comment,
7. Tool: transition Jira issue to Ready for Review.

This keeps simple Jira state changes deterministic while still supporting
agentic work when interpretation or implementation is required.

---

## 10. Naming Policy

### 10.1 Keep `Tool`

MoonMind should keep **Tool** as the user-facing Step Type for typed executable
operations.

Preferred terms:

1. Tool,
2. Typed Tool,
3. Executable Tool,
4. Tool Definition.

Avoid using **Script** for this concept. Script implies arbitrary code or shell
execution and weakens the desired distinction between typed, governed operations
and ad hoc commands.

Avoid using **Executable** as the main UI label. It is accurate as an adjective
but awkward as a Step Type and can imply binaries rather than typed product
operations.

### 10.2 Use `Step Type` in UI

Preferred UI label:

```text
Step Type
```

Avoid:

1. Capability,
2. Activity,
3. Invocation,
4. Command,
5. Script.

These terms may still appear in narrow technical contexts, but they should not be
used as the primary user-facing discriminator for steps.

### 10.3 Keep `Activity` Temporal-specific

Activity means Temporal Activity. It should remain an implementation concept in
Temporal design docs and worker execution code.

Do not use Activity as the Step Type label.

---

## 11. API Shape

The desired API shape is explicit and discriminated.

```ts
type StepType = "tool" | "skill" | "preset";

type BaseStep = {
  id: string;
  title?: string;
  type: StepType;
  source?: StepSource;
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
};

type Step = ToolStep | SkillStep | PresetStep;
```

For executable submission, the accepted shape is normally:

```ts
type ExecutableStep = ToolStep | SkillStep;
```

Preset expansion APIs may accept `PresetStep` and return concrete executable
steps plus provenance metadata.

---

## 12. Preset Management vs Preset Use

Preset management and preset use are separate experiences.

Preset management lives in the Presets section:

1. catalog browsing,
2. create/edit/version,
3. governance and lifecycle,
4. save-from-task,
5. audit and usage inspection.

Preset use lives inside step authoring:

1. add or edit a step,
2. choose `Step Type = Preset`,
3. select a preset,
4. configure inputs,
5. preview generated steps,
6. apply into executable steps.

There should not be a separate Presets section for choosing and applying a preset
to the current task. The Presets section is management-only.

---

## 13. Proposal and Promotion Semantics

Task proposals must preserve executable intent.

When a proposal is created from preset-derived work, it may carry preset
provenance, but the stored promotable task payload should already be executable
and flattened by default.

Promotion must not silently re-expand a live preset catalog entry. If the user
wants to refresh a proposal or draft to the latest preset version, that must be an
explicit action with preview and validation.

Rules:

1. Stored proposals should contain executable Tool and Skill steps.
2. Preset provenance may be preserved as metadata.
3. Promotion validates the reviewed flat payload.
4. Promotion does not require live preset lookup for correctness.
5. Refreshing from a preset catalog is explicit, not automatic.

---

## 14. Migration Guidance

The implementation may migrate in phases.

### Phase 1: UI terminology

1. Rename the step selector label to **Step Type**.
2. Offer Tool, Skill, and Preset in the same step editor.
3. Keep existing backend fields where necessary.
4. Normalize UI copy so Tool means typed operation, not arbitrary script.

### Phase 2: Draft model normalization

1. Introduce explicit `step.type` in draft state.
2. Model Tool, Skill, and Preset sub-payloads separately.
3. Preserve compatibility with existing `skillId`, `tool`, and template fields.
4. Add validation that rejects invalid mixed-type steps.

### Phase 3: Preset expansion normalization

1. Make Preset an authoring-time placeholder.
2. Expand presets into concrete Tool and Skill steps.
3. Preserve source metadata on expanded steps.
4. Ensure submission payloads are executable without live preset lookup.

### Phase 4: Runtime contract convergence

1. Compile executable steps into the canonical plan format.
2. Pin tool and skill registry snapshots where required.
3. Align proposal promotion, task editing, and execution reconstruction with the
   Step Type model.

---

## 15. Non-Goals

1. Redefining Temporal Activity semantics.
2. Replacing the plan executor.
3. Making presets hidden runtime work.
4. Introducing arbitrary shell scripts as a first-class Step Type.
5. Removing legacy compatibility readers immediately.
6. Requiring users to understand worker capability placement to author ordinary
   steps.
7. Treating Tool and Skill as the same thing merely because both may eventually
   map into plan nodes.

---

## 16. Open Design Decisions

### Q1: Should linked presets exist?

Desired default: no. Presets expand into concrete steps at authoring time.

A future linked-preset mode may exist, but it must be explicit and visibly
different from ordinary preset application. It would need separate rules for
version pinning, drift detection, refresh behavior, validation, and audit.

### Q2: Should the API use `step.type` or `step.action.kind`?

Preferred user-facing term: `Step Type`.

Preferred desired-state payload in this document: `step.type`.

`step.action.kind` remains a reasonable internal alternative if implementation
constraints require nesting execution configuration under `action`. The UI should
still say Step Type.

### Q3: Should `tool` be renamed to `script` or `executable`?

Desired answer: no.

Keep `tool` as the user-facing Step Type and define it as a typed executable
operation. Use `Typed Tool` or `Executable Tool` in technical docs when extra
precision is needed. Avoid `Script` for governed operations.
