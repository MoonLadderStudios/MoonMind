# Story Breakdown: Task Architecture (Control Plane)

Source design: `docs/Tasks/TaskArchitecture.md`
Original source document reference path: `docs/Tasks/TaskArchitecture.md`
Story extraction date: 2026-05-08T07:45:02+00:00
Requested output mode: jira

## Design Summary

TaskArchitecture.md defines the desired-state Mission Control task control plane: users author task intent, the control plane normalizes it into artifact-first, target-aware, preset-resolved task contracts, and Temporal executes those contracts durably. The design emphasizes explicit attachment binding, authoritative snapshots, compile-time preset composition, distinct full-retry versus failed-step resume workflows, checkpoint-backed resume, MoonMind-owned APIs, and operator-visible diagnostics without relying on raw workflow history parsing.

## Coverage Points

- **DESIGN-REQ-001 - Task-first control plane** (requirement, 3.1 Task-first control plane): Users author tasks while the control plane translates intent into execution contracts and the execution plane owns lifecycle progression.
- **DESIGN-REQ-002 - Artifact-first binary handling** (constraint, 3.2 Artifact-first binary handling; 11 Invariants): Binary inputs are artifacts represented by lightweight refs, never workflow-history or instruction-text bytes.
- **DESIGN-REQ-003 - Explicit attachment targets** (requirement, 3.3 Explicit target binding; 6 Canonical task-shaped contract): Each input attachment binds to the task objective or a declared step, and binding survives every flow.
- **DESIGN-REQ-004 - Authoritative task input snapshots** (state-model, 3.4 Durable reconstruction; 5.5 Snapshot durability; 7 Snapshot, full retry, and Resume architecture): Edit, rerun, full retry, and resume reconstruct authored input from durable snapshots rather than lossy projections.
- **DESIGN-REQ-005 - Text and structured inputs stay separate** (constraint, 3.5 Separation of text from structured inputs; 11 Invariants): Instruction text remains text, images remain structured inputs, and derived image context is a secondary artifact.
- **DESIGN-REQ-006 - Authoring validation and placement** (requirement, 5.1 Authoring and validation): The Create page/control plane validates repository, runtime, publish mode, dependencies, attachments, and branch/publish placement.
- **DESIGN-REQ-007 - Artifact upload orchestration** (integration, 5.2 Artifact upload orchestration; 9 Artifact and authorization boundary): The browser creates upload intents, completes uploads before submission, rejects incomplete uploads, and submits structured refs only.
- **DESIGN-REQ-008 - Task contract normalization** (requirement, 5.3 Task contract normalization; 6 Canonical task-shaped contract): The task-shaped payload preserves task fields, step identity/order, runtime/publish intent, branch, dependencies, attachments, Jira provenance, and preset metadata.
- **DESIGN-REQ-009 - Compile-time preset composition** (requirement, 5.4 Preset compilation; 11 Invariants): Recursive presets are authoring objects resolved before execution; workers consume flattened steps without live preset catalog lookup.
- **DESIGN-REQ-010 - Preset provenance durability** (state-model, 5.3-5.5; 6 Canonical task-shaped contract): Snapshots and payloads preserve authored preset bindings, include-tree summaries, source provenance, detachment state, and final order.
- **DESIGN-REQ-011 - Single authored branch semantics** (requirement, 6 Canonical task-shaped contract): task.git.branch is the only authored branch field; publish mode determines PR base versus branch update semantics.
- **DESIGN-REQ-012 - Distinct full recovery workflows** (requirement, 3.6; 5.7; 7.1; 7.2): Edit task, exact full rerun, and resume are explicit intents with distinct data mutation and execution behavior.
- **DESIGN-REQ-013 - Resume eligibility evidence** (state-model, 5.7; 7.3; 8.5): Resume requires backend evidence: snapshot, pinned workflow/run IDs, failed-step ledger state, completed outputs, checkpoint, and plan identity.
- **DESIGN-REQ-014 - Resume preserves original inputs** (constraint, 3.6; 7.3; 11 Invariants): Resume cannot edit or silently mutate instructions, steps, attachments, runtime, publish mode, branch, dependencies, or preset metadata.
- **DESIGN-REQ-015 - Resume imports preserved progress** (requirement, 7.3; 8.6): A resumed execution imports completed prior steps, retries the failed step first, and continues later steps normally.
- **DESIGN-REQ-016 - Resume fails explicitly on invalid restoration** (constraint, 3.6; 7.3; 8.6): Missing, stale, unauthorized, corrupted, or inconsistent resume evidence must block Resume or fail before execution, never full-rerun silently.
- **DESIGN-REQ-017 - MoonMind.Run workflow responsibilities** (requirement, 8.1; 12.1): MoonMind.Run owns progression, prepare orchestration, context generation, target-aware context delivery, ledgers, checkpoints, and full/resumed starts.
- **DESIGN-REQ-018 - Target-aware prepare materialization** (artifact, 8.2 Prepare responsibilities): Prepare downloads attachments, writes manifests, materializes stable workspace files, emits target-aware context artifacts, and fails on invalid preparation.
- **DESIGN-REQ-019 - Scoped step and child context** (integration, 8.3; 8.4; 12.2): Step runtimes and AgentRun children receive only relevant objective/current-step context and cannot redefine target binding.
- **DESIGN-REQ-020 - Artifact authorization boundary** (security, 9 Artifact and authorization boundary): Browsers use MoonMind APIs, preview/download is authorized by ownership, and workers use service credentials.
- **DESIGN-REQ-021 - Runtime and prompt boundary** (integration, 10 Runtime and prompt boundary): Adapters may realize normalized intent and artifact refs as text-first or multimodal payloads without inventing new target rules.
- **DESIGN-REQ-022 - Server-defined policy and MoonMind APIs** (security, 11 Invariants): Attachment policy is server-defined and enforced by browser/API; browsers do not call Jira, storage, or provider file endpoints directly.
- **DESIGN-REQ-023 - Operator observability** (observability, 5.6 User-facing reads; 13 Observability and operator surfaces): Details and diagnostics expose target-aware metadata, refs, recovery provenance, and failure phases without raw history parsing.
- **DESIGN-REQ-024 - Subsystem documentation boundary** (constraint, 1 Purpose; 14 Boundary with page-level and subsystem docs): This document is the architecture contract; detailed UI, image, skill, Temporal, ledger, and rerun behavior belongs in related docs.
- **DESIGN-REQ-025 - No hidden retargeting or semantic drift** (constraint, 11 Invariants): Reordering, presets, text changes, aliases, or migration layers must not silently retarget attachments or alter objective-versus-step meaning.

## Ordered Story Candidates

### STORY-001: Normalize task-shaped submissions with explicit attachment targets

Short name: `task-contract-targeting`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 3.1 Task-first control plane, 3.3 Explicit target binding, 5.1 Authoring and validation, 5.3 Task contract normalization, 6 Canonical task-shaped contract, 11 Invariants
Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-025

As a Mission Control user, I want submitted tasks to preserve objective text, steps, repository/runtime/publish choices, dependencies, Jira provenance, and objective- or step-scoped attachments so execution receives exactly the task I authored.

Independent test: Submit representative task payloads through the control-plane API and assert normalized contracts preserve targets and reject hidden retargeting or targetBranch aliases.

Acceptance criteria:
- Create/edit/rerun submission accepts valid task-shaped payloads with objective-scoped and step-scoped attachment refs.
- The normalized payload preserves attachment arrays, step IDs/order, runtime intent, publish mode, task.git.branch, dependencies, and Jira provenance where supported.
- New payloads do not emit targetBranch; task.git.branch carries the single authored branch semantics.
- Reordering steps, changing text, or applying authored data cannot silently retarget attachments.
- Invalid repository, runtime, publish, dependency, attachment policy, or target-binding inputs fail explicitly.

Dependencies: None

### STORY-002: Route binary inputs through authorized artifact refs

Short name: `artifact-input-boundary`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 3.2 Artifact-first binary handling, 5.2 Artifact upload orchestration, 9 Artifact and authorization boundary, 11 Invariants
Coverage IDs: DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, DESIGN-REQ-022

As an operator, I want browser-selected binary inputs uploaded, authorized, finalized, and submitted as lightweight artifact refs so workflow histories and instructions never contain image bytes or storage credentials.

Independent test: Exercise complete and incomplete upload flows; assert only refs enter task payloads and authorization blocks invalid preview/download/materialization.

Acceptance criteria:
- Binary input bytes are stored as artifacts and never embedded in workflow history or inline instruction text.
- Incomplete or invalid uploads are rejected before execution submission.
- Execution submission includes only structured attachment refs.
- Browser preview/download use MoonMind APIs authorized by execution ownership and view permissions.
- Worker materialization uses service credentials and execution authorization.

Dependencies: STORY-001

### STORY-003: Persist authoritative task snapshots for reconstruction

Short name: `task-snapshot-durability`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 3.4 Durable reconstruction, 5.5 Snapshot durability, 7 Snapshot, full retry, and Resume architecture, 11 Invariants
Coverage IDs: DESIGN-REQ-004, DESIGN-REQ-010

As a user retrying or reviewing a task, I want MoonMind to reconstruct the original authored task from a durable snapshot so edit, rerun, and resume flows cannot lose task intent.

Independent test: Submit tasks with attachments and preset provenance, then reload reconstruction from snapshots after derived projections are altered or unavailable.

Acceptance criteria:
- Submission persists an authoritative task input snapshot with all section 7 fields.
- Edit and full retry initial browser state comes from the snapshot, not derived projections.
- Snapshot reconstruction preserves attachment target binding exactly.
- Snapshot reconstruction preserves pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Attachment-aware executions without reconstructible snapshots are explicitly degraded.

Dependencies: STORY-001

### STORY-004: Compile recursive task presets before execution

Short name: `preset-compilation`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 5.4 Preset compilation, 5.3 Task contract normalization, 6 Canonical task-shaped contract, 8 Execution-plane responsibilities, 11 Invariants
Coverage IDs: DESIGN-REQ-009, DESIGN-REQ-010

As a task author using presets, I want recursive preset includes resolved into final ordered steps before execution while MoonMind keeps provenance for audit and safe reconstruction.

Independent test: Submit nested preset tasks and assert worker-facing steps are resolved while provenance reconstructs the authored include tree without live catalog reads.

Acceptance criteria:
- Recursive preset include trees are validated before execution contract finalization.
- Manual and preset-derived steps are flattened into deterministic final submitted order.
- authoredPresets and steps[].source preserve IDs/slugs, versions, aliases, include paths, mappings, original step IDs, and detachment state.
- Workers receive resolved steps and do not expand presets or read the live preset catalog.
- Already submitted work can be reconstructed after live preset catalog changes.

Dependencies: STORY-001, STORY-003

### STORY-005: Prepare target-aware inputs for step execution

Short name: `target-aware-prepare`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 3.5 Separation of text from structured inputs, 8.1 Workflow responsibilities, 8.2 Prepare responsibilities, 8.3 Step execution responsibilities, 8.4 Child workflow responsibilities, 10 Runtime and prompt boundary, 12 Workload-specific behavior
Coverage IDs: DESIGN-REQ-005, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021

As a runtime step, I want prepared context to include relevant objective inputs and only my step-scoped inputs so attachments are materialized, contextualized, and passed across workflow boundaries without leakage.

Independent test: Run a task with objective and multiple step images through prepare and one AgentRun child; assert scoped manifests/context refs and unchanged instruction text.

Acceptance criteria:
- Prepare downloads objective-scoped and step-scoped attachments and writes a canonical manifest.
- Raw files are materialized into stable workspace locations and image context artifacts are produced as secondary artifacts.
- Instruction fields remain textual.
- Each step receives relevant objective context plus only its own step-scoped context by default.
- AgentRun children receive only the prepared context relevant to the represented step.
- Runtime adapters may consume generated context or raw refs but must not invent new targeting rules.

Dependencies: STORY-001, STORY-002

### STORY-006: Expose distinct full retry recovery actions

Short name: `full-retry-actions`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 3.6 Failed-step resume is not full rerun, 5.7 Failed-task recovery orchestration, 7.1 Editable full retry, 7.2 Exact full rerun, 11 Invariants
Coverage IDs: DESIGN-REQ-012, DESIGN-REQ-014

As a user recovering from failure, I want Edit task and Rerun to be explicit full-task retry choices so I can either change the authored task or retry it exactly without importing partial progress.

Independent test: Invoke Edit task and Rerun from a failed execution and assert both start from the beginning with the correct edited or unchanged snapshot input.

Acceptance criteria:
- Failed task details expose Edit task, Rerun, and Resume as separate actions only when backend capability fields are true.
- Edit task opens edit-for-rerun mode from the authoritative snapshot and permits normal task input edits.
- Edited full retry creates a new from-beginning execution with its own snapshot.
- Exact full rerun starts from the beginning using original task input unchanged.
- Neither full retry path imports completed progress from the failed source run.
- Original failed execution state remains immutable.

Dependencies: STORY-003

### STORY-007: Gate Resume on durable checkpoint evidence

Short name: `resume-eligibility-checkpoints`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 5.7 Failed-task recovery orchestration, 7.3 Resume from failed step, 8.5 Resume checkpoint responsibilities, 11 Invariants
Coverage IDs: DESIGN-REQ-013, DESIGN-REQ-016

As an operator, I want Resume offered only when MoonMind can prove the failed step and completed work are recoverable from pinned run evidence.

Independent test: Create complete and incomplete failed-run fixtures; assert capability fields and resume validation allow only complete authorized plan-consistent evidence.

Acceptance criteria:
- Resume eligibility is computed by the backend, not inferred by UI.
- Eligibility requires original snapshot, pinned source workflowId/runId, failed-step ledger identity, completed-step refs, workspace/branch/commit checkpoint, and plan identity/digest.
- Checkpoint creation and writes are idempotent.
- Large or binary checkpoint content remains behind refs.
- Resume requests fail explicitly before execution when evidence is missing, stale, unauthorized, corrupted, or inconsistent.

Dependencies: STORY-003, STORY-005

### STORY-008: Execute Resume from the failed step only

Short name: `resume-execution`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 3.6 Failed-step resume is not full rerun, 7.3 Resume from failed step, 8.6 Resume execution responsibilities, 11 Invariants, 12.1 MoonMind.Run
Coverage IDs: DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017

As a user pressing Resume, I want MoonMind to restore completed work, retry the last failed step, and continue later steps without silently rerunning preserved steps or changing original input.

Independent test: Run a resumed execution from checkpointed failure; assert prior steps are preserved, the failed step executes first, and invalid restoration never full-reruns.

Acceptance criteria:
- Resume uses the original task input snapshot unchanged and exposes no editable authoring form in v1.
- The resumed execution validates checkpoint source, snapshot, and plan identity before executing any step.
- The runtime restores workspace/branch/commit state immediately before the failed step.
- Completed prior steps are preserved with source workflowId, runId, logical step ID, and attempt provenance.
- Preserved outputs are injected so failed and downstream steps observe continuous-run contracts.
- The failed step is retried as the first newly executed step and later steps execute normally.
- Restoration failure does not fall back to full rerun or re-execute preserved steps.

Dependencies: STORY-007

### STORY-009: Show attachment and recovery diagnostics by target

Short name: `operator-diagnostics`
Source reference: `docs/Tasks/TaskArchitecture.md`; sections: 5.6 User-facing reads, 13 Observability and operator surfaces, 14 Boundary with page-level and subsystem docs
Coverage IDs: DESIGN-REQ-023, DESIGN-REQ-024

As an operator inspecting task details, I want target-aware attachment metadata, generated context refs, recovery provenance, and explicit failure phases so I can understand outcomes without parsing raw workflow history.

Independent test: Open task detail/edit/rerun fixtures for attachment tasks and resumed runs; assert target grouping, refs, provenance, and failure phases are visible without raw history parsing.

Acceptance criteria:
- Task detail exposes attachment metadata by objective and step target.
- Diagnostics identify upload, validation, materialization, or context generation failures and which target failed.
- Step-aware surfaces identify current step attachment context separately from unrelated inputs.
- Task detail identifies resumed executions and preserved prior steps reused from source.
- Failed Resume diagnostics identify checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution phases.
- Detailed behavior remains delegated to related subsystem docs.

Dependencies: STORY-002, STORY-003, STORY-007, STORY-008

## Coverage Matrix

- **DESIGN-REQ-001 - Task-first control plane**: STORY-001
- **DESIGN-REQ-002 - Artifact-first binary handling**: STORY-002
- **DESIGN-REQ-003 - Explicit attachment targets**: STORY-001
- **DESIGN-REQ-004 - Authoritative task input snapshots**: STORY-003
- **DESIGN-REQ-005 - Text and structured inputs stay separate**: STORY-005
- **DESIGN-REQ-006 - Authoring validation and placement**: STORY-001
- **DESIGN-REQ-007 - Artifact upload orchestration**: STORY-002
- **DESIGN-REQ-008 - Task contract normalization**: STORY-001
- **DESIGN-REQ-009 - Compile-time preset composition**: STORY-004
- **DESIGN-REQ-010 - Preset provenance durability**: STORY-003, STORY-004
- **DESIGN-REQ-011 - Single authored branch semantics**: STORY-001
- **DESIGN-REQ-012 - Distinct full recovery workflows**: STORY-006
- **DESIGN-REQ-013 - Resume eligibility evidence**: STORY-007
- **DESIGN-REQ-014 - Resume preserves original inputs**: STORY-006, STORY-008
- **DESIGN-REQ-015 - Resume imports preserved progress**: STORY-008
- **DESIGN-REQ-016 - Resume fails explicitly on invalid restoration**: STORY-007, STORY-008
- **DESIGN-REQ-017 - MoonMind.Run workflow responsibilities**: STORY-005, STORY-008
- **DESIGN-REQ-018 - Target-aware prepare materialization**: STORY-005
- **DESIGN-REQ-019 - Scoped step and child context**: STORY-005
- **DESIGN-REQ-020 - Artifact authorization boundary**: STORY-002
- **DESIGN-REQ-021 - Runtime and prompt boundary**: STORY-005
- **DESIGN-REQ-022 - Server-defined policy and MoonMind APIs**: STORY-002
- **DESIGN-REQ-023 - Operator observability**: STORY-009
- **DESIGN-REQ-024 - Subsystem documentation boundary**: STORY-009
- **DESIGN-REQ-025 - No hidden retargeting or semantic drift**: STORY-001

## Dependencies

- **STORY-001** depends on: None
- **STORY-002** depends on: STORY-001
- **STORY-003** depends on: STORY-001
- **STORY-004** depends on: STORY-001, STORY-003
- **STORY-005** depends on: STORY-001, STORY-002
- **STORY-006** depends on: STORY-003
- **STORY-007** depends on: STORY-003, STORY-005
- **STORY-008** depends on: STORY-007
- **STORY-009** depends on: STORY-002, STORY-003, STORY-007, STORY-008

## Out Of Scope

- No spec.md files or specs/ directories are produced during breakdown.
- Detailed Create page behavior remains in docs/UI/CreatePage.md.
- Detailed image subsystem behavior remains in docs/Tasks/ImageSystem.md.
- Detailed Temporal, run history, and step ledger behavior remains in related Temporal docs.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Original Design Text

```markdown
# Task Architecture (Control Plane)

Status: Active
Owners: MoonMind Engineering
Last updated: 2026-05-06

## 1. Purpose

This document defines the high-level desired-state control-plane architecture for MoonMind tasks.

It maps how the control plane translates user intent from Mission Control — including:

- task objective text
- step-authored instructions
- objective-scoped and step-scoped input attachments
- runtime and publish choices
- repository and single authored branch selection
- agent skill selection intent
- presets, Jira imports, and dependency declarations

into durable execution under Temporal.

This document is architectural and declarative. Detailed page behavior belongs in `docs/UI/CreatePage.md`. Detailed image-input behavior belongs in `docs/Tasks/ImageSystem.md`.

---

## 2. System snapshot

MoonMind uses a Temporal-backed execution model in which Mission Control acts as the control plane.

The control plane already centers on these product objects:

- `MoonMind.Run` as the standard task execution workflow
- first-class artifacts for large or binary inputs and outputs
- step-authored tasks rather than opaque queue jobs
- reusable task presets
- runtime and provider selection intent
- durable execution actions such as pause, resume, cancel, approve, and rerun

Desired-state additions clarified by this document:

- image inputs are first-class structured task inputs
- attachment targeting is explicit and durable
- presets are recursively composable authoring objects resolved entirely in the control plane
- create, edit, and rerun preserve attachment bindings through an authoritative task input snapshot
- submitted tasks preserve authored preset metadata and flattened step provenance alongside resolved execution payloads
- runtime preparation and prompt composition are target-aware rather than attachment-bucket-driven
- failed-task recovery has two explicit user workflows:
  - edit the task input and retry the whole task from the beginning
  - press **Resume** to retry the last failed step with the work completed before that step preserved
- failed-step resume depends on durable step ledgers, output refs, and workspace checkpoints rather than log parsing or UI reconstruction

---

## 3. Core architectural principles

### 3.1 Task-first control plane

The user authors tasks, not workflow internals.

Rules:

- the Create page defines user intent in task terms
- the control plane translates that task intent into execution-plane contracts
- the execution plane owns lifecycle progression, retries, waiting, and history

### 3.2 Artifact-first binary handling

Rules:

- binary inputs are stored as artifacts
- binary inputs are referenced in execution contracts by lightweight refs
- binary inputs are not embedded in workflow histories or text instructions

### 3.3 Explicit target binding

Rules:

- an input attachment must belong to an explicit target
- the supported target kinds are:
  - task objective target
  - step target
- target binding must survive create, edit, rerun, prepare, prompt composition, and detail rendering

### 3.4 Durable reconstruction

Rules:

- task input reconstruction uses an authoritative snapshot
- text-only reconstruction is insufficient for attachment-aware tasks
- silent loss of attachment bindings is a contract violation

### 3.5 Separation of text from structured inputs

Rules:

- instruction text remains text
- images remain structured inputs
- derived image context is a secondary artifact, not the instruction field itself

### 3.6 Failed-step resume is not full rerun

Rules:

- failed-task recovery has two separate workflows, and the user's chosen workflow is explicit
- **Edit and retry whole task** loads the original task snapshot into the authoring UI, permits edits, and starts execution from the beginning
- **Resume** does not open an authoring form; it retries the last failed step using the original task input and the durable work completed before that step
- Resume must never silently edit instructions, steps, attachments, runtime, publish mode, branch, dependencies, or preset metadata
- Resume is available only when the platform can identify the failed step and restore the work completed before it from durable evidence
- if the prior work cannot be restored faithfully, Resume must be unavailable or fail explicitly with an operator-readable reason

---

## 4. High-level architecture

```mermaid
flowchart LR
    U[Authenticated User] --> UI[Mission Control Create Page]

    subgraph Control Plane
        UI --> API[Executions API]
        UI --> ART[Artifact API]
        UI --> JIRA[Jira Browser API]
        API --> SNAP[Authoritative Task Input Snapshot]
        API --> PROF[Provider Profile + Runtime Defaults]
        API --> PRESETS[Task Preset APIs]
    end

    subgraph Execution Plane
        API -.-> RUN[MoonMind.Run]
        RUN --> PREP[Prepare / Artifact Activities]
        RUN --> VISION[Vision Context Activity]
        RUN --> STEP[Planner or Step Runtime]
        RUN --> CHILD[MoonMind.AgentRun child workflow]
        RUN --> CKPT[Step Ledger + Resume Checkpoints]
    end

    subgraph Blob Storage
        ART -.-> S3[(Artifact Store)]
        PREP -.-> S3
        VISION -.-> S3
    end
```

Key boundary:

- the control plane owns authoring intent, artifact refs, target binding, runtime choice, preset compilation, and snapshot durability
- the execution plane owns lifecycle, step execution, step ledger state, and resume checkpoint production over already resolved payloads
- runtime adapters own provider-specific realization details

---

## 5. Control-plane responsibilities

The control plane is responsible for all of the following.

### 5.1 Authoring and validation

- render the Create page
- validate repository, runtime, publish mode, dependencies, and attachment policy
- collect text fields, preset state, Jira imports, and input attachments into a coherent draft
- render repository, Branch, and Publish Mode together in the Steps card. `Publish Mode` remains submission data; only its visual placement changes.

### 5.2 Artifact upload orchestration

- create upload intents through MoonMind artifact APIs
- upload browser-selected files before execution submission
- finalize artifact creation and reject incomplete uploads
- submit only structured attachment refs to the execution API

### 5.3 Task contract normalization

- normalize the task-shaped payload
- preserve `task.inputAttachments` and `task.steps[].inputAttachments`
- preserve step identity and order
- preserve runtime and publish intent
- preserve authored preset binding metadata, flattened step provenance, manual and preset-derived step order, and fully resolved execution payloads
- preserve Jira provenance when those contracts allow it

### 5.4 Preset compilation

Preset compilation is a control-plane phase that completes before execution contract finalization.

Rules:

- presets are authoring objects, not execution-plane instructions
- recursive preset composition is resolved in the control plane
- preset compilation validates the include tree before producing worker-facing steps
- compilation flattens manual and preset-derived steps into the final submitted order
- compilation preserves provenance for preset-derived steps and detached template state
- the resolved execution payload must remain executable without live preset catalog lookup

### 5.5 Snapshot durability

- persist an authoritative task input snapshot for edit and rerun
- reconstruct from that snapshot rather than from lossy derived projections
- preserve attachment target binding in the snapshot
- preserve pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order in the snapshot

### 5.6 User-facing reads

- expose previews and downloads through MoonMind APIs
- surface attachment metadata by target in detail, edit, and rerun flows
- expose enough diagnostics for operators to understand attachment-related failures

### 5.7 Failed-task recovery orchestration

The control plane exposes distinct recovery actions instead of treating every recovery path as a generic rerun.

Rules:

- failed task details may expose **Edit task**, **Rerun**, and **Resume** as separate actions when their capability fields are true
- **Edit task** on a failed execution is the editable full retry path; submitting it creates a new execution from the beginning with a new authoritative task input snapshot
- **Rerun** is the exact full retry path; it starts from the beginning using the original task input without edits
- **Resume** is the failed-step recovery path; it starts a linked follow-up execution that imports completed prior progress and retries the last failed step
- Resume eligibility must be computed by the backend, not inferred by the UI
- Resume eligibility requires, at minimum:
  - an authoritative original task input snapshot
  - a pinned source `workflowId` and `runId`
  - a step ledger that identifies the last failed step
  - durable refs for all completed steps before the failed step
  - a workspace, branch, commit, or equivalent checkpoint representing the state immediately before the failed step
  - a plan identity or digest proving that the restored progress belongs to the same planned step graph
- Resume requests must be rejected explicitly when any required evidence is missing, stale, unauthorized, or inconsistent

---

## 6. Canonical task-shaped contract

Representative contract:

```ts
interface TaskInputAttachmentRef {
  artifactId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
}

interface TaskStepSource {
  kind?: "manual" | "preset-derived" | "preset-include" | "detached";
  presetId?: string;
  presetSlug?: string;
  version?: string;
  includePath?: string[];
  originalStepId?: string;
}

interface AuthoredPresetBinding {
  presetId?: string;
  presetSlug?: string;
  version?: string;
  alias?: string;
  includePath?: string[];
  inputMapping?: Record<string, unknown>;
  scope?: string;
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
  skills?: {
    include?: Array<{ name: string }>;
  };
}

interface TaskPayload {
  instructions?: string;
  inputAttachments?: TaskInputAttachmentRef[];
  steps?: TaskStepPayload[];
  authoredPresets?: AuthoredPresetBinding[];
  runtime?: {
    mode?: string;
    profileId?: string;
    model?: string;
    effort?: string;
  };
  publish?: {
    mode?: "none" | "branch" | "pr";
  };
  git?: {
    branch?: string;
  };
  appliedStepTemplates?: unknown[];
  dependsOn?: string[];
}

type TaskRecoveryKind = "exact_full_rerun" | "edited_full_retry" | "resume_from_failed_step";

interface TaskRecoveryProvenance {
  kind: TaskRecoveryKind;
  sourceWorkflowId: string;
  sourceRunId: string;
  requestedBy?: string;
  requestedAt?: string;
}

interface ResumeFromFailedStepRef {
  kind: "resume_from_failed_step";
  sourceWorkflowId: string;
  sourceRunId: string;
  failedStepId: string;
  failedStepAttempt?: number;
  resumeCheckpointRef: string;
  taskInputSnapshotRef: string;
  planRef?: string;
  planDigest?: string;
}

interface TaskPayloadWithRecovery extends TaskPayload {
  recovery?: TaskRecoveryProvenance;
  resume?: ResumeFromFailedStepRef;
}
```

Rules:

- `task.inputAttachments` is the objective-scoped input target
- `task.steps[n].inputAttachments` is the step-scoped input target
- `task.authoredPresets` preserves optional preset binding metadata used to compile the submitted task
- `task.steps[n].source` preserves optional source provenance for manual, preset-derived, included, or detached steps
- these fields are part of the task contract, not incidental UI metadata
- the absence of attachments is valid
- the presence of attachments must be preserved across create, detail, edit, and rerun
- `task.git.branch` is the single authored branch field; new create, edit, and rerun payloads do not include `targetBranch`
- for `publish.mode === "pr"`, `task.git.branch` is the selected repository branch / PR base and the PR head branch is runtime-generated or provider-managed
- for `publish.mode === "branch"`, `task.git.branch` is the branch to update/push
- `Publish Mode` remains part of task submission semantics; only its Create page placement changes
- the execution-facing payload is resolved before workers consume it; `authoredPresets` and `source` metadata are for reconstruction, audit, diagnostics, and safe rerun semantics
- `task.recovery.kind === "edited_full_retry"` or `"exact_full_rerun"` means the new execution starts from the beginning
- `task.resume.kind === "resume_from_failed_step"` means the new execution must restore completed progress from `resumeCheckpointRef` and start at `failedStepId`
- resume provenance must include both `sourceWorkflowId` and `sourceRunId` so a resume is pinned to the exact source run and cannot drift when the logical execution later changes
- resume checkpoint refs are execution-state refs, not editable authoring fields

---

## 7. Snapshot, full retry, and Resume architecture

The original task input snapshot is the authoritative representation of the authored draft.

Rules:

- it must preserve:
  - task objective text
  - objective-scoped attachment refs
  - step text
  - step-scoped attachment refs
  - step order and identity
  - runtime and publish selections
  - repository and single authored branch selection
  - preset application metadata
  - pinned preset bindings
  - include-tree summary
  - per-step provenance
  - detachment state
  - final submitted order after manual and preset-derived steps are flattened
  - dependency declarations that remain part of the editable contract
- edit, exact full rerun, edited full retry, and Resume all depend on this snapshot for the original authored task input
- edit and full retry derive their initial browser state from this snapshot
- Resume reuses this snapshot without presenting it as an editable authoring surface
- edit, rerun, full retry, and Resume must not depend on current live preset catalog correctness to reconstruct already submitted work
- fallback evidence refs may assist diagnostics, but they are not an authoritative replacement for the snapshot
- an attachment-aware execution without a reconstructible snapshot is degraded and must be treated as such explicitly

### 7.1 Editable full retry

Editable full retry is the workflow used when the user wants to change the overall instructions or any other task input and then retry the task.

Rules:

- the Create page opens in edit-for-rerun mode from the authoritative task input snapshot
- the user may edit instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, and other authoring fields subject to normal validation
- submitting the form creates a new execution from the beginning
- the edited execution gets its own authoritative task input snapshot
- the original failed execution, its snapshot, step ledger, artifacts, and checkpoints remain immutable
- no completed execution progress is imported into the edited full retry

### 7.2 Exact full rerun

Exact full rerun is the workflow used when the user wants to retry the whole task with the same original task input.

Rules:

- the original task input snapshot is reused as the execution input
- the task starts from the beginning
- prepare, prompt composition, planning or plan hydration, and all steps run again according to the normal execution path
- no completed execution progress is imported from the failed source run

### 7.3 Resume from failed step

Resume is the workflow used when the user presses **Resume** on a failed task to retry the last failed step with the completed work up to that step preserved.

Rules:

- Resume is not an edit flow and must not allow task input changes in v1
- Resume pins the source execution with both `sourceWorkflowId` and `sourceRunId`
- Resume identifies the last failed step from the source run's step ledger
- Resume creates or resolves a `resumeCheckpointRef` that records the completed steps, their output refs, the prepared input refs, and the workspace or branch state immediately before the failed step
- the new execution imports completed prior steps as preserved progress rather than re-executing them
- the failed step is retried as a new attempt in the new execution
- later steps execute normally after the failed step succeeds
- the task detail view must show preserved prior steps as reused from the source run, not freshly executed by the resumed run
- if checkpoint restoration is incomplete, corrupted, unauthorized, or inconsistent with the original task input and plan digest, Resume must fail explicitly before executing the failed step

Representative resume checkpoint artifact:

```json
{
  "schemaVersion": "v1",
  "source": {
    "workflowId": "mm:source",
    "runId": "source-run-id"
  },
  "taskInputSnapshotRef": "art_original_task_snapshot",
  "planRef": "art_original_plan",
  "planDigest": "sha256:...",
  "failedStep": {
    "logicalStepId": "run-tests",
    "order": 4,
    "attempt": 1,
    "title": "Run test suite"
  },
  "preservedSteps": [
    {
      "logicalStepId": "apply-patch",
      "order": 3,
      "status": "succeeded",
      "sourceAttempt": 1,
      "outputRefs": {
        "outputSummary": "art_step_summary",
        "outputPrimary": "art_step_output"
      }
    }
  ],
  "resumeWorkspace": {
    "kind": "workspace_checkpoint",
    "ref": "art_workspace_before_failed_step"
  }
}
```

---

## 8. Execution-plane responsibilities

The execution plane consumes the normalized task contract after control-plane preset compilation has produced a resolved execution payload.

Rules:

- workers consume resolved steps and structured input refs
- workers do not expand presets
- workers do not read the live preset catalog to recover missing task structure
- workers do not depend on live preset catalog correctness for already submitted work

### 8.1 Workflow responsibilities

`MoonMind.Run` owns:

- durable state progression
- waiting, retry, and cancel semantics
- prepare-time attachment handling
- image context generation orchestration
- passing target-aware context into the relevant planner or step runtime
- preserving step ledger state and refs required for later Resume eligibility

### 8.2 Prepare responsibilities

Prepare owns:

- downloading objective-scoped and step-scoped attachments
- writing a canonical attachments manifest
- materializing raw files into stable workspace locations
- producing target-aware image context artifacts
- failing explicitly when attachment preparation is incomplete or invalid

### 8.3 Step execution responsibilities

Step execution owns:

- consuming task-level objective context when relevant
- consuming only the current step’s step-scoped image context by default
- avoiding accidental leakage of unrelated step attachments into the wrong step execution

### 8.4 Child workflow responsibilities

If a step is executed through `MoonMind.AgentRun`, the parent-child boundary must preserve target-aware prepared context.

Rules:

- parent workflow remains the source of truth for attachment target binding
- child workflows receive only the prepared context relevant to the child step
- child workflow logs and diagnostics do not redefine target binding semantics

### 8.5 Resume checkpoint responsibilities

The execution plane owns the durable evidence that makes Resume truthful.

Rules:

- after prepare succeeds, the workflow must record the prepared input refs needed to avoid repeating preparation during Resume when reuse is safe
- after each step succeeds, the workflow must record bounded step state and semantic output refs needed by downstream steps
- before or after each step boundary, the workflow must record a workspace, branch, commit, or equivalent state checkpoint when the runtime mutates working state
- checkpoint writes must be idempotent because activities and workflow tasks may retry
- checkpoint refs must remain outside large inline workflow histories when they are large or binary
- a completed step without recoverable output refs or state checkpoint evidence is not eligible for Resume preservation

### 8.6 Resume execution responsibilities

When a new execution starts with `task.resume.kind === "resume_from_failed_step"`, `MoonMind.Run` owns:

- loading and validating the resume checkpoint
- verifying the checkpoint source `workflowId`, `runId`, task snapshot, and plan identity
- materializing the restored workspace state before the failed step
- marking completed prior steps as preserved from the source run without re-executing them
- injecting preserved outputs so the failed step and downstream steps observe the same contracts as a continuous run
- retrying the failed step as the first newly executed step of the resumed execution
- producing fresh ledger rows, artifacts, and checkpoints for the retried failed step and all later steps

Rules:

- the execution plane must not silently fall back to full rerun behavior when Resume restoration fails
- the execution plane must not re-execute preserved prior steps unless a future UI explicitly asks for that behavior
- preserved rows must carry provenance back to the source `workflowId`, `runId`, logical step ID, and attempt

---

## 9. Artifact and authorization boundary

The artifact system is the binary boundary of the control plane.

Rules:

- the browser never receives long-lived object-store credentials
- user preview and download are authorized by execution ownership and view permissions
- worker-side download and materialization use service credentials and execution authorization
- artifact links are execution-scoped
- target binding is preserved by task contract and snapshot semantics, not inferred from storage paths alone

Recommended metadata may include:

- target kind
- step reference
- original filename
- source import path such as upload or Jira import

Rules:

- metadata is helpful for observability
- metadata must not be the only place where target meaning exists

---

## 10. Runtime and prompt boundary

The control plane does not dictate provider-native multimodal payloads.

Rules:

- the control plane passes normalized task intent plus artifact refs
- text-first runtimes consume generated image context through the canonical `INPUT ATTACHMENTS` contract
- multimodal runtimes may consume raw image refs through runtime adapters without changing the control-plane task contract
- runtime adapters must not invent new attachment targeting rules that the Create page cannot express

---

## 11. Invariants

The following invariants define the desired-state task system.

1. **No binary payloads in Temporal history**
   Image bytes do not belong in execution histories or inline create payload text.

2. **Explicit attachment targets**
   Every input attachment belongs either to the task objective target or to a declared step target.

3. **No silent attachment loss**
   Create, edit, rerun, and prepare must fail explicitly rather than silently dropping attachments.

4. **Text remains text**
   Instruction fields remain textual authoring surfaces. Images remain structured inputs.

5. **Snapshot-based durability**
   Attachment-aware edit and rerun require an authoritative task input snapshot.

6. **Compile-time preset composition**
   Preset composition is compile-time control-plane behavior. Submitted execution payloads must not require live preset lookup.

7. **Preset provenance durability**
   Task snapshots preserve pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.

8. **Server-defined policy**
   Attachment policy is defined by server configuration and enforced by both browser and API.

9. **MoonMind-owned browser APIs**
   The browser talks only to MoonMind APIs, not directly to Jira, object storage, or provider-specific file endpoints.

10. **Target-aware runtime consumption**
   By default, step execution receives only its own step-scoped attachment context plus relevant objective-scoped context.

11. **No hidden retargeting**
   Reordering steps, applying presets, or changing text must not silently retarget an existing attachment to another step.

12. **Compatibility without semantic drift**
   Compatibility aliases and migration layers may exist, but they must not change the canonical meaning of objective-scoped versus step-scoped attachments.

13. **Explicit recovery intent**
   Full rerun, edited full retry, and Resume are distinct intents. The system must not infer Resume from a generic rerun request.

14. **Resume preserves original inputs**
   Resume uses the original task input snapshot unchanged. Any user edit to instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies requires edited full retry instead.

15. **Resume requires checkpointed progress**
   Resume may be offered only when completed work before the failed step is recoverable from durable step refs and workspace or branch checkpoints.

16. **No silent re-execution of preserved steps**
   Resume must display and treat prior completed steps as preserved from the source run. Re-executing them without explicit user intent is a contract violation.

17. **Pinned resume source**
   Resume must pin both source `workflowId` and source `runId` so recovery cannot drift to a later run of the same logical execution.

---

## 12. Workload-specific behavior

### 12.1 `MoonMind.Run`

This is the canonical attachment-aware task workflow.

Rules:

- attachment-aware task authoring is defined against `MoonMind.Run`
- create, edit, rerun, and detail flows for attachment-aware tasks are all modeled in task-shaped `MoonMind.Run` terms
- `MoonMind.Run` is the canonical workflow that produces step ledger state and resume checkpoints for failed-step Resume
- `MoonMind.Run` may start from the beginning for full retry or start at a failed step when given a validated resume checkpoint
- checkpoint durability remains a parent `MoonMind.Run` responsibility even when an individual step delegates work to `MoonMind.AgentRun`

### 12.2 `MoonMind.AgentRun`

This child workflow may execute a specific step.

Rules:

- when used, it consumes prepared context for the step it represents
- it must not redefine or broaden its attachment scope beyond what the parent workflow prepared

### 12.3 Other workflow types

Other workflow types may reuse artifact infrastructure, but they do not redefine the Create-page attachment contract.

---

## 13. Observability and operator surfaces

The architecture must support operator understanding without requiring raw history parsing.

Rules:

- task detail should expose attachment metadata by target
- diagnostics should expose manifest and generated context refs where appropriate
- attachment failures should identify:
  - which target failed
  - whether the failure happened during upload, validation, materialization, or context generation
- step-aware surfaces should identify the current step’s attachment context separately from unrelated step inputs
- task detail should identify resumed executions and show preserved prior steps as reused from the source run
- diagnostics for failed Resume attempts should identify whether the failure happened during checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution

---

## 14. Boundary with page-level and subsystem docs

Use this document to understand the architectural contract.

Use the related docs for detailed behavior:

- `docs/UI/CreatePage.md` for page sections, field behavior, Jira targeting, edit/rerun UX, and validation copy
- `docs/UI/TaskDetailsPage.md` for failed-task action presentation, including **Resume**
- `docs/Tasks/ImageSystem.md` for image-input upload, artifact storage, materialization, context generation, and preview/download behavior
- `docs/Tasks/AgentSkillSystem.md` for skill selection and resolution
- `docs/Temporal/TemporalArchitecture.md` for workflow lifecycle and worker topology
- `docs/Temporal/RunHistoryAndRerunSemantics.md` for Workflow ID, Run ID, full rerun, and Resume identity semantics
- `docs/Temporal/StepLedgerAndProgressModel.md` for step ledger, preserved-step, and resume checkpoint semantics

---

## 15. Summary

MoonMind’s control plane is task-first, artifact-first, and target-aware.

For image inputs that means:

- the user authors text and image inputs in one draft
- the control plane uploads and binds images to explicit targets
- the task contract and authoritative snapshot preserve those bindings
- the execution plane prepares and injects target-aware context
- detail, edit, and rerun surfaces can round-trip the same authored intent without semantic loss
- failed-task **Resume** can retry the last failed step only when durable checkpoints can restore the work completed before that step

That is the desired-state task architecture contract.
```
