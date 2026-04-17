# Preset Composability

This document proposes desired-state updates for the MoonMind documents that should change to make preset composability a first-class feature.

Design posture throughout:

- Presets are authoring-time control-plane objects.
- A preset may contain concrete steps and preset includes.
- Preset composition resolves recursively at expansion time.
- Execution still consumes a flat `PlanDefinition` / flat step set.
- Runtime and workflow behavior do not become recursively preset-aware.
- The authoritative edit/rerun snapshot preserves both:
  - the authored preset composition tree, and
  - the flattened resolved steps that were actually submitted.

---

## 1. `docs/Tasks/TaskPresetsSystem.md`

### 1.1 Update the purpose and relationship-to-plans sections

Replace the current “preset = parameterized blueprint of steps” posture with:

> A **Preset** is a versioned, parameterized authoring object that may contain:
>
> - concrete step blueprints
> - includes of other presets
>
> Preset composition is a control-plane compile step. It is resolved recursively into a flat `PlanDefinition` before execution. Temporal workflows and runtime adapters never execute nested presets directly.

Add these explicit rules:

- Preset composition is **compile-time only**.
- The executor consumes only flattened plan nodes and edges.
- Preset includes exist to reduce duplication and support reuse.
- Preset composition does **not** introduce inheritance-based runtime behavior.
- Preset composition does **not** require runtime catalog lookups after plan creation.

### 1.2 Add terminology

Add these terms:

| Term | Definition |
| --- | --- |
| **Preset Include** | A step-list entry that references another preset and contributes its resolved steps into the parent preset. |
| **Expansion Tree** | The recursively resolved tree of root preset plus included presets before flattening. |
| **Flattened Plan** | The ordered set of concrete plan nodes produced after recursive expansion. This is the only plan form sent to execution. |
| **Preset Provenance** | Metadata carried on flattened steps describing which preset/include path produced the step. |
| **Detachment** | The state in which a step that originated from a preset has been manually edited and no longer exactly matches the authored preset expansion. |

### 1.3 Update goals and non-goals

Add goals:

- allow presets to reuse other presets without copy-paste duplication
- preserve deterministic expansion despite recursive composition
- preserve enough source metadata for edit, rerun, reapply, and observability
- make preset reuse first-class without changing runtime semantics

Add non-goals:

- no runtime execution of nested presets
- no child-preset step override/patch semantics in v1
- no implicit inheritance where a parent preset edits the internals of a child preset
- no silent re-expansion of already submitted runs against newer preset versions

### 1.4 Replace the preset version model for `steps`

Keep the top-level `steps` field, but redefine it as an ordered list of **preset entries**.

Canonical union:

```yaml
steps:
  - kind: step
    slug: analyze
    title: Analyze issue
    instructions: |-
      Review the request:
      {{ inputs.feature_request }}
    skill:
      id: moonspec-analyze
      args: {}
    annotations:
      phase: analysis

  - kind: include
    preset: review-core
    version: 1.2.0
    as: core
    inputs:
      feature_request: "{{ inputs.feature_request }}"
      repository: "{{ inputs.repository }}"
    annotations:
      role: reusable-review-block

  - kind: step
    slug: publish
    title: Publish branch or PR
    instructions: |-
      Publish the result using the selected mode.
    skill:
      id: publish
      args: {}
```

Rules:

- `kind: step` produces one concrete step blueprint.
- `kind: include` references another preset version and contributes its resolved steps.
- `version` on an include is required in the stored immutable preset version contract. Expansion-time “latest active” resolution is not a durable storage contract.
- `as` is the include alias used for provenance, namespacing, debugging, and deterministic ID generation.
- A parent preset may include the same child preset multiple times only when each include has a distinct `as`.
- Includes are resolved server-side only.
- Parent presets may pass mapped inputs to child presets.
- Child presets may not be modified in-place by parent overrides in v1.

### 1.5 Add scope and visibility rules for includes

Add:

- `GLOBAL` presets may include only `GLOBAL` presets.
- `PERSONAL` presets may include:
  - visible `GLOBAL` presets
  - the owner’s own `PERSONAL` presets
- A preset must never include a preset the current author or expansion context cannot read.
- Expansion fails fast if an included preset is missing, inactive, unreadable, or version-incompatible.

### 1.6 Add recursive expansion rules

Add a new section, **Composable expansion pipeline**:

1. Resolve the root preset version.
2. Validate root inputs.
3. Build an expansion tree by recursively resolving `kind: include` entries.
4. Detect cycles before rendering child content.
5. Enforce maximum expansion depth.
6. Enforce maximum flattened step count after expansion.
7. Render all concrete step blueprints in resolved input context.
8. Flatten the expansion tree into ordered concrete plan steps.
9. Assign deterministic resolved step IDs.
10. Attach preset provenance metadata to each flattened step.
11. Infer edges from flattened ordering unless explicit dependency support is later enabled.
12. Store:
   - the flattened `PlanDefinition`
   - the expansion tree or expansion summary artifact
   - audit metadata linking the root preset application to the flattened result

### 1.7 Add cycle detection and limits

Add explicit validation rules:

- direct and indirect cycles are invalid
- cycle detection uses the effective include chain, not just slug equality
- the error must report the full include path that formed the cycle
- the server must reject expansions that exceed:
  - `max_include_depth`
  - `max_expanded_step_count`
  - `max_total_rendered_instruction_bytes`

Representative failure:

```json
{
  "error": "PRESET_INCLUDE_CYCLE",
  "message": "Preset include cycle detected",
  "details": {
    "path": [
      "review-with-publish@1.0.0",
      "review-core@1.2.0",
      "review-with-publish@1.0.0"
    ]
  }
}
```

### 1.8 Replace deterministic step ID rules

Replace index-only language with a path-aware rule:

> Deterministic resolved step IDs are derived from:
>
> - the root preset slug and version
> - the include alias path from the root to the producing blueprint
> - the producing blueprint step slug or local index
> - the canonical root input hash

Representative rule:

```text
tpl:{root_slug}:{root_version}:{include_path}:{local_step_slug}:{input_hash8}
```

Representative flattened IDs:

- `tpl:review-with-publish:1.0.0:core.analyze:a1b2c3d4`
- `tpl:review-with-publish:1.0.0:core.test:a1b2c3d4`
- `tpl:review-with-publish:1.0.0:publish:a1b2c3d4`

Rules:

- IDs must be stable for the same stored preset version + include tree + inputs.
- IDs must not depend on transient database ordering.
- The include alias path is part of the stable identity of a resolved step.

### 1.9 Add flattened step provenance

Add a canonical provenance shape on each resolved step before plan storage:

```json
{
  "source": {
    "kind": "preset_step",
    "rootPreset": {
      "slug": "review-with-publish",
      "version": "1.0.0"
    },
    "includePath": [
      { "slug": "review-with-publish", "version": "1.0.0", "alias": "root" },
      { "slug": "review-core", "version": "1.2.0", "alias": "core" }
    ],
    "blueprintStepSlug": "test"
  }
}
```

Rules:

- source provenance is carried for edit/rerun/debugging
- source provenance must not change execution semantics
- manual steps remain `source.kind = "manual"`

### 1.10 Update the expand API contract

Extend the expand response to optionally return both tree and flat views.

Representative response:

```json
{
  "composition": {
    "rootPreset": { "slug": "review-with-publish", "version": "1.0.0" },
    "includes": [
      {
        "alias": "core",
        "preset": { "slug": "review-core", "version": "1.2.0" },
        "expandedStepCount": 3
      }
    ],
    "expandedStepCount": 4,
    "expansionDigest": "presetexp:sha256:abc123..."
  },
  "plan": {
    "plan_version": "1.0",
    "metadata": {
      "title": "review-with-publish v1.0.0",
      "created_at": "2026-04-16T00:00:00Z"
    },
    "nodes": [
      {
        "id": "tpl:review-with-publish:1.0.0:core.test:a1b2c3d4",
        "title": "Run tests",
        "tool": { "type": "skill", "name": "repo.run_tests", "version": "1.2.0" },
        "inputs": {},
        "source": {
          "kind": "preset_step",
          "includePath": [
            { "slug": "review-with-publish", "version": "1.0.0", "alias": "root" },
            { "slug": "review-core", "version": "1.2.0", "alias": "core" }
          ],
          "blueprintStepSlug": "test"
        }
      }
    ],
    "edges": []
  },
  "planArtifactRef": "art:sha256:789abc...",
  "warnings": []
}
```

Rules:

- `plan` remains the execution contract
- `composition` is control-plane introspection and preview data
- preview callers may request tree-only, flat-only, or both
- expansion output must be deterministic for the same pinned preset versions and inputs

### 1.11 Add save-as-preset preservation semantics

Add:

- When saving current steps as a preset, MoonMind should preserve preset includes where intact preset provenance still exists.
- Detached or custom steps are serialized as `kind: step`.
- A saved preset must not claim an include if the saved content no longer matches the included preset’s authored expansion.
- Preservation of composition is opportunistic and exact-match based, not fuzzy.

### 1.12 Add execution boundary language

Add a direct statement:

> The executor never interprets nested preset semantics. Recursive composition is completely resolved before the `PlanDefinition` artifact is written. Runtime execution remains flat, deterministic, and plan-based.

---

## 2. `docs/UI/CreatePage.md`

### 2.1 Update product stance

Replace “presets are reusable step blueprints” with:

> Presets are reusable authoring objects that may contain concrete steps and other presets. The Create page may present preset composition to the user, but execution is always based on flattened resolved steps.

Add:

- the Create page is allowed to show preset-group structure
- the draft must preserve both authored preset binding state and flat step state
- manual steps and preset-derived steps may coexist in one ordered draft

### 2.2 Update the draft model

Replace `appliedTemplates` emphasis with first-class preset bindings.

Add representative browser model:

```ts
interface PresetIncludeRef {
  slug: string;
  version: string;
  alias: string;
}

interface AppliedPresetBinding {
  localId: string;
  slug: string;
  version: string;
  alias: string;
  inputs: Record<string, unknown>;
  expansionDigest: string;
  state: "applied" | "needs_reapply" | "partially_detached" | "detached";
  includeTree: Array<{
    slug: string;
    version: string;
    alias: string;
    children: unknown[];
  }>;
}

interface StepSource {
  kind: "manual" | "preset";
  bindingLocalId?: string;
  includePath?: PresetIncludeRef[];
  blueprintStepSlug?: string;
  detached?: boolean;
}

interface StepDraft {
  localId: string;
  id: string;
  title: string;
  instructions: string;
  source: StepSource;
  skillId: string;
  skillArgs: string;
  skillRequiredCapabilities: string;
  attachments: DraftAttachment[];
}
```

Rules:

- `AppliedPresetBinding` is the draft-level source of preset authoring truth
- `StepDraft.source` preserves resolved provenance for each step
- the browser must not rely on text matching alone to understand preset origin
- a preset-derived step may become detached without deleting the overall binding

### 2.3 Replace “template-bound” with “preset-bound”

Rename the behavioral concept throughout:

- `template-bound step` → `preset-bound step`
- compatibility aliases may exist in implementation, but docs should use preset language

Desired-state rules:

- a preset-bound step is one whose authored content still exactly matches the resolved output of a preset binding
- manual edits detach only the edited step by default
- a root preset binding becomes `partially_detached` when some but not all of its produced steps are detached
- a root preset binding becomes `detached` only when none of its produced steps remain preset-bound

### 2.4 Add preset group rendering rules

Under the step editor and preset area, add:

- the UI may render applied presets as collapsible groups
- nested preset structure may be shown in preview or edit affordances
- the canonical editable execution order remains the flattened step list
- users may insert manual steps:
  - before a preset group
  - between preset groups
  - after a preset group
- users do not directly drag child steps across preset boundaries while they remain preset-bound unless the system detaches those steps

### 2.5 Replace preset application behavior

Current desired state should become:

- selecting a preset alone does not modify the draft
- `Apply` creates a new root preset binding in the draft
- applying a preset resolves the preset composition recursively through the server
- the page receives:
  - preset binding metadata
  - the flattened resolved step list
  - source provenance for each step
- when the form contains only the initial placeholder step, that placeholder may be replaced
- otherwise, the newly applied preset contributes a new grouped block of resolved steps at the insertion point

Add a new preview rule:

- before apply, the UI should be able to show:
  - a structured preset composition preview
  - the flattened resolved step preview

### 2.6 Add reapply semantics for composed presets

Reapply rules:

- changing root preset input values marks the root binding `needs_reapply`
- if the root preset passes those values into included presets, the root binding still remains the single dirty object the user re-applies
- manual edits to child steps do not automatically disappear on input change
- `Reapply preset` must clearly state whether it will:
  - update only still-bound steps, or
  - replace the full contributed block
- desired-state behavior: reapply updates only steps that are still preset-bound; detached steps remain untouched unless the user explicitly requests full replacement

Representative copy:

- `Reapply will update 3 preset-bound steps. 1 customized step will remain unchanged.`

### 2.7 Add save-current-steps-as-preset behavior

Desired-state rules:

- save-as-preset preserves intact preset includes where exact provenance remains available
- detached/custom steps are saved as concrete `kind: step` entries
- the UI should not silently flatten intact composition during save
- the user may optionally choose “flatten before save” as an explicit advanced option, not the default

### 2.8 Update edit and rerun contract

Add:

- edit and rerun reconstruct:
  - applied preset bindings
  - binding state (`applied`, `needs_reapply`, `partially_detached`, `detached`)
  - flattened steps with source provenance
- edit and rerun must not degrade every preset-authored run into manual flat steps if preserved authoring metadata exists
- if preset binding metadata is unavailable but flat step provenance exists, the page may reconstruct a degraded flat draft and must surface that degradation explicitly
- if neither binding metadata nor usable source provenance exists, the page falls back to manual flat-step reconstruction and must not imply that reapply is available

Representative degraded copy:

- `This task was reconstructed from flat steps. Original preset binding metadata is unavailable, so preset reapply is not supported for this draft.`

### 2.9 Update submission contract

Add a sharper boundary:

- the Create page submits flat resolved steps for execution readiness
- the same request or its authoritative snapshot also preserves authored preset bindings and step provenance for edit/rerun UX
- the execution plane must not require a preset catalog lookup to start the run
- preset composition is therefore preserved for UX and observability, not as a runtime dependency

### 2.10 Add testing requirements

Add tests for:

1. recursively composed preset preview returns both tree and flat views
2. applying a preset with included presets creates one root binding and multiple resolved steps
3. cycle or missing-include errors are surfaced without corrupting the draft
4. editing one preset-derived step detaches only that step
5. reapply updates only still-bound steps
6. save-as-preset preserves intact composition when available
7. edit/rerun reconstructs preset binding state when snapshot metadata exists
8. degraded reconstruction falls back to flat steps with explicit user notice

---

## 3. `docs/Tasks/TaskArchitecture.md`

### 3.1 Update the system snapshot

Add these desired-state bullets:

- presets are recursively composable authoring objects
- preset composition resolves entirely in the control plane
- the authoritative snapshot preserves both authored preset bindings and flattened resolved steps
- execution remains flat-step / flat-plan based

### 3.2 Add a control-plane compilation phase

Add a new subsection after authoring and validation:

> **Preset compilation**
>
> The control plane resolves preset bindings, expands included presets recursively, validates the expansion tree, flattens the result into concrete steps, and preserves provenance metadata before the execution contract is finalized.

Rules:

- recursive preset resolution is a control-plane concern
- cycle detection and include visibility checks happen before execution submission
- runtime workers never perform recursive preset expansion
- edit/rerun reconstruction relies on snapshot data, not on re-reading live preset definitions by default

### 3.3 Update task contract normalization

Extend normalization responsibilities:

- preserve authored preset binding metadata in the authoritative snapshot
- preserve flattened step provenance
- normalize manual and preset-derived steps into one ordered execution contract
- guarantee that the execution payload is already fully resolved

### 3.4 Update the canonical task-shaped contract

Add optional authored preset metadata and per-step provenance:

```ts
interface AppliedPresetBindingSnapshot {
  bindingId: string;
  slug: string;
  version: string;
  alias: string;
  inputs: Record<string, unknown>;
  expansionDigest: string;
  state: "applied" | "needs_reapply" | "partially_detached" | "detached";
}

interface TaskStepSource {
  kind: "manual" | "preset";
  bindingId?: string;
  includePath?: Array<{ slug: string; version: string; alias: string }>;
  blueprintStepSlug?: string;
  detached?: boolean;
}

interface TaskStepPayload {
  id?: string;
  title?: string;
  instructions?: string;
  inputAttachments?: TaskInputAttachmentRef[];
  source?: TaskStepSource;
  skill?: {
    id?: string;
    args?: Record<string, unknown>;
    requiredCapabilities?: string[];
  };
}

interface TaskPayload {
  instructions?: string;
  inputAttachments?: TaskInputAttachmentRef[];
  steps?: TaskStepPayload[];
  authoredPresets?: AppliedPresetBindingSnapshot[];
  runtime?: {
    mode?: string;
    profileId?: string;
    model?: string;
    effort?: string;
  };
}
```

Rules:

- `authoredPresets` is for control-plane durability and reconstruction
- `steps[]` is the execution-ready flattened step list
- `steps[].source` is optional execution metadata and is ignored for runtime semantics
- absence of `authoredPresets` is valid for manual tasks

### 3.5 Add snapshot durability requirements

Strengthen the snapshot section:

The authoritative snapshot must preserve:

- root preset bindings and pinned versions
- include-tree summary or equivalent binding reconstruction data
- per-step source provenance
- detachment state when a preset-derived step has been customized
- the final flat step order actually submitted

Rules:

- a run must remain executable even if the preset catalog later changes
- edit/rerun reconstruction should prefer the preserved snapshot over live preset lookups
- live preset lookups are a preview/reapply aid, not the authoritative record of what was submitted

### 3.6 Add execution-plane boundary language

Add a strong statement:

> The execution plane receives already resolved steps. It does not execute preset trees, perform recursive preset expansion, or depend on the preset catalog for correctness.

### 3.7 Update invariants

Add two new invariants:

11. **Preset composition is compile-time only**  
   Nested presets are resolved in the control plane before plan submission.

12. **Execution does not depend on live preset lookup**  
   A submitted run remains executable and reconstructible from its stored snapshot and artifacts even if preset definitions later change.

---

## 4. `docs/Tasks/SkillAndPlanContracts.md`

### 4.1 Update the document boundary

Add:

- preset composition is an authoring concern, not an execution concern
- this document defines only the flattened execution contract after preset expansion
- plans must not contain unresolved preset includes

### 4.2 Add a rule under plan production

Add a subsection:

> **Preset expansion boundary**
>
> A preset compiler or expansion service may produce a `PlanDefinition`, but a `PlanDefinition` itself contains only concrete nodes and edges. Nested preset includes are invalid inside the runtime plan contract.

Rules:

- `PlanDefinition.nodes[]` must be executable plan nodes only
- preset include objects must not appear in the stored plan artifact
- recursive composition must be fully resolved before validation and execution

### 4.3 Extend the plan node contract with optional provenance

Add optional node metadata:

```json
{
  "id": "tpl:review-with-publish:1.0.0:core.test:a1b2c3d4",
  "title": "Run tests",
  "tool": {
    "type": "skill",
    "name": "repo.run_tests",
    "version": "1.2.0"
  },
  "inputs": {},
  "source": {
    "kind": "preset_step",
    "binding_id": "preset-binding-1",
    "include_path": [
      { "slug": "review-with-publish", "version": "1.0.0", "alias": "root" },
      { "slug": "review-core", "version": "1.2.0", "alias": "core" }
    ],
    "blueprint_step_slug": "test",
    "detached": false
  }
}
```

Rules:

- `source` metadata is optional
- `source` metadata is for UI, observability, and reconstruction
- executor scheduling, readiness, concurrency, and failure handling do not depend on `source`

### 4.4 Update validation rules

Add to plan validation:

- plans must not contain unresolved preset include entries
- if `source.kind = "preset_step"`, its fields must be structurally valid when present
- invalid source provenance is a validation error only if the plan claims preset provenance; absence of provenance is allowed

### 4.5 Update examples and semantics

Add a note after the DAG example:

> A plan may originate from manual step authoring, preset expansion, or another plan-producing tool. Regardless of origin, the plan contract is always the same flattened node-and-edge graph.

### 4.6 Add an explicit execution invariant

Add:

- nested preset semantics do not exist at runtime
- runtime behavior is defined only by nodes, edges, policies, artifacts, and tool contracts
- preset provenance may be visible, but it is never executable logic

---

## 5. `docs/UI/MissionControlArchitecture.md`

### 5.1 Update the implementation snapshot / purpose

Add preset-composition scope to Mission Control:

- Mission Control must support preview, edit, and detail rendering of preset-derived work without making preset composition a runtime concept
- Create and detail surfaces may show preset provenance and grouping
- list pages remain flat and high-signal; they do not need nested preset structure

### 5.2 Update task detail page architecture

Add to the detail page behavior:

- task detail may show a compact preset provenance summary in the metadata or authoring section
- step rows may show chips such as:
  - `Manual`
  - `Preset: review-with-publish`
  - `Preset path: review-with-publish > core > test`
- the Steps section remains execution-first; preset grouping is explanatory metadata, not the primary ordering model

Rules:

- detail pages do not reconstruct runtime semantics from preset provenance
- detail pages should make it easy to understand whether a run came from manual authoring or preset composition
- if the run was created from preserved binding metadata, the Edit action should reopen a composed draft when possible

### 5.3 Update submit integration

Add:

- `/tasks/new` may preview composed presets through the preset expansion API before create
- direct execution submit still uses flat resolved task intent
- Mission Control must not submit unresolved preset includes as runtime work

### 5.4 Update artifact / evidence posture

Add:

- preset expansion tree artifacts or expansion summaries may be shown as secondary evidence when available
- the canonical execution evidence remains:
  - flat steps
  - logs
  - diagnostics
  - output artifacts

### 5.5 Update compatibility vocabulary

Add a small clarification:

- user-facing term: `preset`
- internal expansion/debug term: `preset binding` or `preset provenance`
- do not call preset includes “subtasks”, “sub-plans”, or separate workflow runs

---

## 6. `docs/Tasks/TaskProposalSystem.md`

### 6.1 Update core invariants

Add these invariants:

11. When proposal payloads preserve preset-derived authoring intent, that metadata must be advisory UX/reconstruction metadata, not a runtime dependency.
12. Proposal promotion must not require a live preset catalog lookup for correctness.
13. If proposal payloads carry preset provenance, the flattened step contract remains the canonical execution input.

### 6.2 Update canonical payload contract

Add optional preserved preset authoring metadata:

```json
{
  "taskCreateRequest": {
    "type": "task",
    "payload": {
      "repository": "owner/repo",
      "task": {
        "instructions": "Resolved task objective text.",
        "authoredPresets": [
          {
            "bindingId": "preset-binding-1",
            "slug": "review-with-publish",
            "version": "1.0.0",
            "alias": "root",
            "inputs": {
              "feature_request": "Review and publish this change"
            },
            "expansionDigest": "presetexp:sha256:abc123...",
            "state": "partially_detached"
          }
        ],
        "steps": [
          {
            "id": "tpl:review-with-publish:1.0.0:core.test:a1b2c3d4",
            "instructions": "Run tests",
            "source": {
              "kind": "preset",
              "bindingId": "preset-binding-1",
              "includePath": [
                { "slug": "review-with-publish", "version": "1.0.0", "alias": "root" },
                { "slug": "review-core", "version": "1.2.0", "alias": "core" }
              ],
              "blueprintStepSlug": "test",
              "detached": false
            }
          }
        ]
      }
    }
  }
}
```

Rules:

- proposal payloads may preserve authored preset metadata
- proposal payloads must still carry execution-ready flat steps
- preserving preset metadata is optional and must not weaken execution determinism

### 6.3 Add promotion rules for preset-derived proposals

Add:

- promotion preserves `authoredPresets` and per-step provenance by default
- promotion validates the flat task payload as usual
- promotion does **not** re-expand live presets by default
- a future explicit “refresh against latest preset version” workflow may exist, but it is not the default promotion path

This avoids drift between proposal review time and proposal promotion time.

### 6.4 Update proposal-generation guidance

Add:

- generators may preserve preset provenance from the parent run when it materially improves later review/edit ergonomics
- generators should not fabricate preset bindings for work that was not actually authored from a preset
- if the generated proposal does not preserve reliable preset metadata, it should emit a normal flat task payload only

### 6.5 Update UI/observability section

Add:

- proposal detail may show whether the proposed work is:
  - manual
  - preset-derived with preserved binding metadata
  - preset-derived but flattened-only
- proposal promotion UI may disclose when preset reapply/edit affordances are expected to survive after promotion

---

## 7. `docs/Temporal/101-PlansOverview.md` (or the equivalent plans overview/index doc)

This update is intentionally minimal.

Add one alignment paragraph near the overview/index section for plans:

> Preset composition belongs to the control plane and is resolved before `PlanDefinition` creation. Plans remain flattened execution graphs of concrete nodes and edges. For authoring-time composition semantics, see `docs/Tasks/TaskPresetsSystem.md`. For runtime plan semantics, see `docs/Tasks/SkillAndPlanContracts.md`.

Add or update cross-links so the overview makes this boundary obvious.

---

## 8. Cross-document invariants that should match everywhere

These statements should read consistently across the updated documents.

### 8.1 Authoring vs execution boundary

- Presets may include other presets.
- Recursive preset composition is resolved before execution.
- Execution consumes flat steps / flat plans only.

### 8.2 Determinism boundary

- Submitted work must remain executable without a live preset catalog lookup.
- Pinned preset versions and stored snapshots/provenance must be sufficient for reconstruction.

### 8.3 UX durability boundary

- Edit/rerun should preserve composed authoring intent when snapshot metadata exists.
- If it does not exist, the system may degrade to flat-step editing, but must say so explicitly.

### 8.4 No inheritance-based child override in v1

- Parent presets may include child presets and pass inputs.
- Parent presets do not patch or surgically override child internals in v1.

### 8.5 Provenance is metadata, not runtime logic

- Preset provenance exists for explanation, debugging, and reconstruction.
- Executor semantics do not depend on provenance.

---

## 9. Recommended implementation order implied by the docs

1. Update `TaskPresetsSystem.md` first.
2. Update `CreatePage.md` next.
3. Update `TaskArchitecture.md` to codify snapshot + control-plane compilation.
4. Update `SkillAndPlanContracts.md` to harden the execution boundary.
5. Align `MissionControlArchitecture.md`.
6. Align `TaskProposalSystem.md`.
7. Add the one-paragraph overview/index clarification in the plans overview doc.
