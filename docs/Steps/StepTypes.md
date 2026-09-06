# Step Types

**Document Class:** Canonical declarative  
**Status:** Desired-state architecture  
**Owners:** MoonMind Engineering (Workflow Platform + UI)  
**Last Updated:** 2026-09-06

Related: `docs/Workflows/WorkflowPresetsSystem.md`, `docs/Workflows/RequiredCapabilities.md`, `docs/Workflows/WorkflowPublishing.md`, `docs/UI/CreatePage.md`, `docs/Steps/SkillSystem.md`, `docs/Steps/InputSchemaGuidance.md`, `docs/Steps/JiraIntegration.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Tools/DockerComposeUpdateSystem.md`

## 1. Purpose

Step Type determines what a user-visible unit of work represents, its capability selector, task-specific input schema, validation, expansion, and runtime-plan mapping. Ordinary authors do not need to understand activities, registries, worker placement, or compiled publication roles.

Repository/source context, branch context, and publication are selected once at workflow level. Step Type does not create another place to edit those values.

## 2. Desired-State Summary

The canonical normalized Step Types are:

```text
tool | skill | preset
```

Tool selects a typed executable operation. Skill selects reusable agent behavior. Preset selects an authoring composition that expands into executable Tool/Skill steps before runtime.

Configured Presets can remain unexpanded in the draft and submitted authoring request, but the backend expands them before creating the executable plan. Runtime correctness does not depend on a later live catalog lookup.

Instructions, Managed Agent, External Agent, and controlled script-runner labels may be friendly shortcuts where useful. Instructions/agent shortcuts normalize to Skill execution; a controlled script runner is a typed Tool. They are not additional canonical Step Types. Runtime choice is configuration, not Step Type.

## 3. Terminology

| Term | Meaning |
| --- | --- |
| Workflow Execution | Durable user-request execution under its admitted context/policy |
| Step | User-visible unit within a draft or executable workflow |
| Step Type | tool, skill, or preset |
| Capability | Selected catalog item, not the primary Step Type UI label |
| Tool | Typed, schema-backed, policy-checked executable operation |
| Skill | Portable agent-facing instructions/behavior for reasoning and open-ended work |
| Preset | Parameterized authoring composition expanded into concrete steps |
| Input Schema | Expected task values and declared semantic context bindings |
| UI Schema | Optional safe presentation hints, not authority |
| Expansion | Backend transformation of definition, validated inputs, and context |
| Provenance | Selected definition evidence, authored task inputs, ancestry, and bound-context derivation |
| Plan | Runtime artifact compiled from executable steps |
| Activity | Temporal implementation concern, not a user Step Type |

RequiredCapabilities are execution requirement tokens derived from work and effect roles, distinct from catalog capabilities. They do not grant permissions or justify duplicate selectors.

## 4. Core Invariants

Each authored step has one type and matching payload. The selected capability supplies normalized schema/default/binding metadata. Shared forms and registered widgets render task inputs without Skill/Preset-name branches. Tools and Skills execute; Presets expand first. Source/contract evidence and ancestry remain pinned for admitted execution.

Arbitrary shell snippets and Temporal Activities are not Step Types. Historical payload readers may decode old shapes, but new authoring does not expose legacy aliases as additional authority.

Every step consumes the workflow's admitted repository/branch/publication context or an explicitly supported distinct target role. No independent generic step-level publish, repository, or branch override exists in guided, Advanced, or raw JSON. A compiler-generated execution argument can carry the projected value without becoming authored state.

## 5. Shared Capability Input Contract

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

Tools, Skills, and Presets share standard schema types, required fields/alternatives, properties, arrays, enums, formats, and safe namespaced semantics. Skills may omit a schema and retain instruction-driven use under admitted context.

A validated `x-moonmind-context-binding` projects authoritative repository.name, repository.branch, or publication.policy as supported by the consumer. Bind before required/type validation; show a source explanation rather than another editor. Unknown authority-bearing bindings or caller-supplied duplicates fail before mutation. Harmless unknown presentation hints can degrade safely.

Do not infer equivalence from names. An issue repository, comparison branch, source repository, and independently admitted destination can have genuinely different roles. Their metadata and labels must express that distinction.

## 6. Step Type Taxonomy

### 6.1 tool

Tools represent explicit bounded operations such as fetching/transitioning an issue, posting an authorized comment, managed PR creation, controlled tests, or a governed deployment action. Definitions declare name, input/output schemas, authorization, worker requirements, retry/effect policy, execution binding, and errors.

```json
{
  "id": "move-jira-to-in-progress",
  "title": "Move Jira issue to In Progress",
  "type": "tool",
  "tool": {
    "name": "jira.transition_issue",
    "inputs": {"issueKey": "MM-123", "targetStatus": "In Progress"}
  }
}
```

A PR-creation Tool is a publication effect under the same workflow policy, not a way to bypass None. Typed external effects still require their own authorization.

### 6.2 skill

Skills represent interpretation, planning, implementation, synthesis, troubleshooting, and other agent work. They may use Tools internally, but their primary behavior remains the resolved portable Skill.

Task inputs include instructions, issue/artifact references, constraints, verification or finish choices, and permitted task specialization. Equivalent repository/branch/publication values are bound from workflow context. Runtime/Profile authoring follows its single existing selector, not a required per-Skill configuration chain.

```json
{
  "id": "implement-issue",
  "title": "Implement Jira issue",
  "type": "skill",
  "skill": {
    "name": "code.implementation",
    "inputs": {"issueKey": "MM-123", "instructions": "Implement the issue."}
  }
}
```

The repository and intended publishing outcome are outside this task-specific input object. The adapter can project portable arguments at execution with compiler-owned provenance.

#### Instructions shortcut

Instructions normalizes to a Skill such as the supported default agent behavior, not a fourth canonical type or a grant inferred from prose.

#### Managed and external agent shortcuts

Supported agent shortcuts normalize to Skill execution under the normal runtime-selection contract. The admitted Runtime/Profile remains authoritative. Publishing does not select a new host, native semantic implementation, or billing route.

### 6.3 preset

Presets select known reusable compositions, including Jira/GitHub implementation, breakdown, review/fix, documentation orchestration, and deployment verification. They collect task inputs and optional meaningful task settings, not duplicated execution-context controls.

```json
{
  "id": "apply-jira-orchestrate",
  "title": "Jira Orchestrate",
  "type": "preset",
  "preset": {
    "slug": "jira-orchestrate",
    "inputs": {"jira_issue": {"key": "MM-123"}}
  },
  "expansionState": "not_expanded"
}
```

Generated steps retain preset identity, content/contract evidence, task input snapshot, ancestry, and bound context. A generated read-only/local-None role does not overwrite the root policy.

## 7. Schema-Driven Step Editor UX

A step shows title, Step Type, capability selector, task-specific schema inputs, source explanations, and supported advanced options. Changing type preserves compatible task values and clearly handles meaningful discarded inputs. It never leaves hidden stale repository or publishing values behind.

### 7.1 Step type picker

Tool means run a typed operation; Skill means ask an agent to use reusable behavior; Preset means configure a reusable composition. The label is Step Type, not Capability, Activity, Invocation, Command, or Script.

### 7.2 Capability picker

Group Tools by domain/integration, Skills by compatible behavior, and Presets by composition. Selecting an item loads normalized metadata and validates compatibility with the one workflow policy.

### 7.3 Generic widget registry

Shared registered components include text, textarea/markdown, numeric, checkbox, select/multi-select, structured JSON, issue/PR pickers, repository/branch pickers, supported profile/model controls, and artifact references. Widget availability does not authorize duplicating an existing workflow-level choice.

Guided Skill mode exposes required unbound fields and required alternatives. Optional task fields appear in Advanced. Bound fields remain read-only explanations in both. Errors focus the actual editable workflow source/target or task input.

## 8. Preset Input and Expansion Contract

Presets declare enough normalized metadata for deterministic input collection and expansion. Jira issue widgets are chosen from schema semantics, not a page branch for a known slug.

### 8.1 Apply

The backend binds current context, validates task inputs and policy, expands the selected definition, and inserts concrete steps with provenance. Generated task content may remain editable; execution authority does not become independently editable per step.

### 8.2 Reapply

Reapply uses selected definition evidence and current authored inputs/context. Explain replacement of edited generated content. Invalidate stale lookups and generated repository/base/policy projections. Never resurrect an old literal embedded in instructions after the visible context changed.

### 8.3 Submit-time auto-expansion

The backend validates non-preset context, resolves bindings/defaults, expands all unresolved and nested presets, validates concrete steps and publication/child handoffs, and admits execution. Field-addressable errors preserve safe user inputs. A client cannot bypass this path by submitting forged resolved values.

## 9. Runtime and Payload Contract

### 9.1 Draft payload

Drafts contain Tool, Skill, or Preset steps with task inputs and selected definition evidence.

### 9.2 Create Workflow submission payload

Unresolved Presets are accepted only as authoring convenience. The shared compiler resolves them before runtime and retains one authored repository/branch/publication snapshot.

### 9.3 Runtime payload

Runtime steps are executable Tools/Skills with pinned definition, bound input, target, and effect-role evidence. They do not rediscover a live preset or Skill definition to alter admitted authority.

### 9.4 Runtime plan mapping

Tools map to typed operation plan nodes; Skills map to ordinary agent execution nodes/subordinate workflows; Presets have no unresolved runtime node by default. Temporal Activities/child workflows and runtime mechanics remain implementation details.

### 9.5 Publication roles are not Step Types

A step can be read-only, a tracker effect, a coordinator, an implementation candidate producer, or a Skill-owned existing-PR operation. The compiler derives these responsibilities from validated definitions and the single scope policy. They are not another picker or independent publish override.

An Assess → Implement → Test → Update Documentation chain shares one policy. Read-only steps need no None selector. A supported composition may publish at declared stages, but each effect has one owner and exact target. Conflicting owners/objectives require a compatible declared composition or separate workflows, not last-step-wins or most-permissive mode selection.

User-facing Auto/default resolves declared behavior. Internal Auto remains the Skill-owned evidence protocol. Coordinator-local None does not disable publishing children. Explicit scope None cannot run a push-requiring resolver, including fix_only, without a genuinely separate compatible non-publishing objective.

## 10. Validation Rules

### 10.1 Common validation

Require stable local identity, title/generated label, one type, matching payload, valid task values/context bindings, supported definition evidence, and pre-effect policy compatibility.

### 10.2 Tool validation

The Tool exists, inputs validate, current authority/readiness and retry/effect policy are known, and forbidden fields are absent. Arbitrary shell snippets are accepted only through an explicitly supported typed bounded command contract.

### 10.3 Skill validation

The Skill or documented automatic Skill selector resolves, optional input schema validates, required context is present, runtime compatibility is qualified, and Tool/approval policy can be enforced. Automatic Skill selection, publishing Auto, and model selection are separate concepts and cannot grant one another authority.

### 10.4 Preset validation

The preset exists, task inputs/context validate, expansion is deterministic, generated steps satisfy their own contracts, and limits/warnings are enforced. Unknown authority-sensitive defaults/bindings and incompatible policy/handoffs block rather than falling back.

Errors identify the editable task field or workflow source control and the affected step/ancestry. A missing bound repository is not a request to edit a hidden repository input.

## 11. Jira Example

A Jira workflow can contain trusted fetch and transition Tools, an implementation Skill, test Tools, managed PR publication, and trusted status/comment updates. All share the same context and policy.

A PR-URL-required transition waits for verified managed publication. An already-implemented no-change result uses its explicit trusted completion contract. None does not silently skip required handoffs while reporting full implementation success. Tracker-only work can remain independently authorized under None because code publication and tracker effects are distinct.

## 12. Naming Policy

### 12.1 Keep Tool

Tool means typed governed executable operation. Script/Executable are not replacement canonical type names. A script runner is a Tool only with its typed input, policy, and effect contract.

### 12.2 Use Step Type in UI

Use the concise canonical discriminator. Capability remains an internal/catalog term.

### 12.3 Keep Activity Temporal-specific

Activity names an implementation boundary, never another authoring category.

## 13. API Shape

```ts
type StepType = "tool" | "skill" | "preset";
type StepProvenance = {
  sourceType: "preset" | "manual";
  presetSlug?: string;
  inputSnapshot?: Record<string, unknown>;
  parentPresetPath?: string[];
};
type BaseStep = {id: string; title?: string; type: StepType; provenance?: StepProvenance};
type ToolStep = BaseStep & {type: "tool"; tool: {name: string; inputs: Record<string, unknown>}};
type SkillStep = BaseStep & {type: "skill"; skill: {name: string; inputs: Record<string, unknown>}};
type PresetStep = BaseStep & {
  type: "preset";
  preset: {slug: string; inputs: Record<string, unknown>};
  expansionState?: "not_expanded" | "applied" | "error";
};
type DraftStep = ToolStep | SkillStep | PresetStep;
type ExecutableStep = ToolStep | SkillStep;
```

These abbreviated authored shapes omit generated contract/content and context derivation evidence for readability. An arbitrary inputs map is still schema/policy validated and cannot carry forbidden repository/branch/publish duplicates.

ExpandPresetRequest carries presetSlug, task inputs, and shared context. Its response contains executable steps, provenance, safe warnings, and effective-policy explanation through the existing contract. Context is not another editable per-step scope.

## 14. Preset Management vs Preset Use

The Presets section owns catalog lifecycle, governance, creation/editing, save-from-workflow, audit, and expansion testing. Using a preset happens in the step editor: select Preset, configure task inputs, Apply or Submit unexpanded.

Saving a composition records role/default requirements rather than copying one run's derived coordinator None into a permanent root default. It cannot hide an independent publication selection in preset inputs.

## 15. Draft and Reconstruction Semantics

Stored executable workflows are flattened and definition-bound. Drafts can retain Presets through the normal expansion path. Runtime/replay never silently refreshes a live catalog entry. Explicit draft refresh preserves task values and revalidates changed context/policy.

Authored, defaulted, bound, and derived values remain distinguishable. Equal historical copies can collapse only with evidence; conflicting copies, mixed per-step policies, old branch pairs, and old Auto/None meanings require the versioned reconstruction contract. No historical artifact is rewritten to look newly authored.

## 16. Migration Guidance

Compatibility is limited to known old inputs/histories with explicit readers, original digest/replay interpretation, and removal conditions. New writers use one Step Type and one workflow context. They do not keep old override aliases as permanent escape hatches. Detailed migration phases belong in issues or `docs/tmp/`, not this declarative specification.

## 17. Non-Goals

This does not replace the plan executor, create hidden runtime preset lookup, add arbitrary scripts or Activities as types, remove required historical readers prematurely, require worker-placement knowledge, collapse Tools and Skills, or create per-capability frontend forms. It does not add another publishing system.

## 18. Design decisions and conformance

The default is expanded presets, explicit step.type, Tool rather than Script, and Instructions as a Skill shortcut. A future linked-preset mode requires its own explicit evidence/refresh contract and is not implied here.

Conformance proves canonical type normalization, shared forms, optional schema use, required alternatives, bound-value error routing, deterministic expansion, pinned runtime definitions, and provenance. It also proves no duplicate context editors, explicit policy preserved through Apply/Reapply and rerun, correct coordinator/read-only/publisher roles, target-derived PR branches, and rejection of incompatible publication/child handoffs before effects. UI labels alone do not establish runtime conformance.
