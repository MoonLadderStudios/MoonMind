# Task Steps System

Status: Draft (Design Target)
Owners: MoonMind Engineering
Last Updated: 2026-02-17

## 1. Purpose

Define the Task Steps extension for MoonMind queue Tasks so one `type="task"` job can execute an ordered sequence of steps, where each step is one runtime invocation (Codex / Gemini / Claude), while preserving:

- One Task = one queue job (single claim, single workspace, single publish decision)
- One set of task-level controls (runtime, model, effort, repo, branches, publish) for the entire run
- Optional step-level overrides for:
  - `instructions`
  - `skill`

This is a declarative design document for steady-state behavior and rollout constraints.

---

## 2. Current-State Snapshot (2026-02-17)

Implemented today:

- Canonical `type="task"` payload normalization is in place.
- Wrapper stages exist and run in order: `moonmind.task.prepare` -> `moonmind.task.execute` -> `moonmind.task.publish`.
- Queue task publish default is `pr` (contract + `/tasks/queue/new` UI default).
- Cooperative cancellation is implemented for running jobs (`cancel` request + worker `cancel/ack`).
- Container execution mode exists under `task.container` and currently runs as a single execute-stage flow.

Not implemented yet:

- `task.steps[]` contract and server validation.
- Per-step execute loop and per-step events/artifacts.
- Queue submit UI steps editor.

This document specifies the target behavior and clarifies integration with the current runtime.

---

## 3. Definitions

**Task**  
A user-submitted unit of work represented as one `type="task"` Agent Queue job.

**Step**  
An ordered execution unit inside a Task. Each step maps to exactly one runtime invocation.

**Task Run**  
The full job lifecycle from claim -> prepare -> execute (all steps) -> publish (optional) -> terminal state.

**Step Execution**  
One runtime invocation with a step-specific assembled prompt.

---

## 4. Goals and Non-Goals

### Goals

1. Execute `N` steps sequentially in one worker claim/session.
2. Allow optional step-specific `instructions` and `skill` overrides.
3. Keep repository, branches, runtime, and publish settings task-scoped.
4. Preserve wrapper-stage contract for prepare/execute/publish.
5. Provide step-level observability through events and artifacts.
6. Preserve current cancellation guarantees during multi-step execution.

### Non-Goals

- Live step editing while a Task is running.
- Parallel step execution.
- Step-level publish/commit (publish remains task-scoped).
- Replacing orchestrator plans.
- Step execution for `task.container.enabled=true` in the first rollout.

---

## 5. High-Level Architecture

### 5.1 One Task Job, Many Steps

A Task remains one queue job claimed once by one worker. The worker executes steps in order in one workspace.

### 5.2 Wrapper Stages Remain the Outer Contract

The outer stage contract remains:

1. `moonmind.task.prepare`
2. `moonmind.task.execute` (runs Step 1..N internally)
3. `moonmind.task.publish` (conditional)

Steps are internal to execute stage, not new queue jobs.

---

## 6. Canonical Payload Contract Extension

### 6.1 Add `task.steps[]` (Optional)

Extend the canonical Task payload with optional `task.steps`.

- If omitted or empty: implicit single-step behavior (current semantics).
- If provided: execute steps in listed order.

### 6.2 Step Schema

```json
{
  "id": "step-1",
  "title": "Optional short label",
  "instructions": "Optional step-specific instructions",
  "skill": {
    "id": "auto",
    "args": {}
  }
}
```

Field rules:

- `id` optional; if absent, worker generates index-based ID.
- `title` optional display metadata.
- `instructions` optional; absent means objective-only continuation.
- `skill` optional; absent means inherit task-level `task.skill`.
- Step-level runtime/model/effort/repo/publish are not allowed.

### 6.3 Task-Level Instructions Stay Required

`task.instructions` remains required and represents the run objective.

### 6.4 Backward Compatibility

- Existing tasks without `task.steps` must continue to run unchanged.
- Producers are not required to send `task.steps`.
- Legacy `codex_exec` / `codex_skill` normalization behavior remains unchanged.

### 6.5 Publish Default Clarification

For canonical `type="task"` jobs, omitted `task.publish.mode` defaults to `pr`.
Task Steps must preserve this default.

---

## 7. Step Prompt Assembly

Each step assembles one runtime invocation prompt with:

- Effective skill: `step.skill.id` -> `task.skill.id` -> `auto`
- Effective instructions:
  - always include Task Objective (`task.instructions`)
  - append Step Instructions when present

### 7.1 Deterministic Runtime-Neutral Prompt Template

```
MOONMIND TASK OBJECTIVE:
{task.instructions}

STEP {i+1}/{N} {stepIdOrIndex} {optionalTitle}:
{step.instructions or "(no step-specific instructions; continue based on objective)"}

EFFECTIVE SKILL:
{effectiveSkillId}

WORKSPACE:
- Repo is already checked out on the working branch.
- Do NOT commit or push. Publish is handled by MoonMind publish stage.
- Skills are available via .agents/skills and .gemini/skills links.
- Write logs to stdout/stderr; MoonMind captures them.
```

If `effectiveSkillId != "auto"`, append:

```
SKILL USAGE:
Use the selected skill's files under .agents/skills/{skill}/ as the procedure for this step.
```

---

## 8. Worker Execution Semantics

### 8.1 Prepare Stage

Prepare responsibilities remain:

- Create workspace directories (`repo/`, `home/`, `skills_active/`, `artifacts/`)
- Clone/fetch repo and resolve branches
- Materialize skills and links
- Write `task_context.json`

Task Steps extension:

- Materialize union of non-`auto` skills referenced by:
  - `task.skill.id`
  - `task.steps[*].skill.id`
- If union is empty, keep shared skill links behavior.

### 8.2 Execute Stage

Execute stage behavior:

1. Resolve steps list:
   - use `task.steps` if non-empty
   - otherwise create implicit single step
2. For each step in order:
   - emit `task.step.started`
   - invoke runtime once with assembled prompt
   - write step logs (and optional step patch)
   - emit `task.step.finished` or `task.step.failed`
3. On first step failure:
   - fail execute stage immediately
   - do not run remaining steps
   - do not run publish stage

### 8.3 Cancellation Semantics (Must Preserve Current Behavior)

While running steps, worker must preserve current cooperative cancellation behavior:

- detect cancellation requests from heartbeat payload (`cancelRequestedAt`)
- interrupt command execution where possible
- stop before next step when cancellation is set
- acknowledge via `/api/queue/jobs/{jobId}/cancel/ack`
- avoid success/failure completion transitions after cancellation acknowledgement

### 8.4 Publish Stage

Publish runs once per Task run after all steps succeed:

- `none`: skip
- `branch`: commit + push
- `pr`: commit + push + PR

Defaults:

- For canonical task payloads, default publish mode remains `pr`.

---

## 9. Events and Artifacts

### 9.1 Required Step Events

Add/emit:

- `task.steps.plan` (step count + IDs)
- `task.step.started`
- `task.step.finished`
- `task.step.failed`

Payload minimum:

- `stepIndex` (0-based)
- `stepId`
- `effectiveSkill`
- `hasStepInstructions`
- `summary` (optional)

Note: existing wrapper stage events remain required (`moonmind.task.prepare`, `moonmind.task.execute`, `moonmind.task.publish`).

### 9.2 Step Artifacts (Recommended)

Per step `i`:

- `logs/steps/step-000{i}.log`
- `patches/steps/step-000{i}.patch` (optional)

Existing task artifacts continue:

- `logs/prepare.log`
- `logs/execute.log`
- `logs/publish.log`
- `patches/changes.patch`
- `task_context.json`
- `publish_result.json`

---

## 10. Task Dashboard UI Changes

### 10.1 Queue Submit UI (`/tasks/queue/new`)

Current state: no steps editor exists yet.

Target changes:

- Add a Steps section with add/remove and optional reorder controls.
- Keep existing task-level fields unchanged.
- Keep publish default as `pr` in UI.

### 10.2 Defaults and Validation

- Task Objective (`task.instructions`) stays required.
- `steps` may be empty (implicit single step).
- If steps exist, UI should prevent completely blank steps where practical.

### 10.3 Example Payload

```json
{
  "type": "task",
  "priority": 0,
  "maxAttempts": 3,
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "targetRuntime": "codex",
    "task": {
      "instructions": "Implement TaskStepsSystem and update tests.",
      "runtime": { "mode": "codex", "model": "gpt-5-codex", "effort": "high" },
      "skill": { "id": "auto", "args": {} },
      "steps": [
        {
          "id": "step-1",
          "title": "Inspect current task/worker architecture",
          "instructions": "Review current TaskArchitecture and identify integration points.",
          "skill": { "id": "auto", "args": {} }
        },
        {
          "id": "step-2",
          "title": "Generate spec using Speckit",
          "skill": { "id": "speckit", "args": {} }
        }
      ],
      "git": { "startingBranch": null, "newBranch": null },
      "publish": { "mode": "pr", "prBaseBranch": null, "commitMessage": null, "prTitle": null, "prBody": null }
    }
  }
}
```

---

## 11. Server and Contract Changes

### 11.1 Task Contract Models

Add:

- `TaskStepSpec`
- `task.steps: list[TaskStepSpec] = []`

### 11.2 Capability Derivation

Preserve existing derivation and extend for steps:

- runtime capability
- `git`
- `gh` when publish mode is `pr`
- `docker` when `task.container.enabled=true`
- union of `requiredCapabilities` from:
  - `task.skill.requiredCapabilities`
  - `task.steps[*].skill.requiredCapabilities`

---

## 12. Worker Notes (Codex First)

### 12.1 Runtime Execution

- Codex path: one `codex exec` invocation per step, same repo/workspace.
- Gemini/Claude paths follow same one-step/one-invocation contract.

### 12.2 Skill Routing

For each step where effective skill is non-auto:

- resolve via skill-first path for that step
- capture step-level metadata in events

### 12.3 Container Tasks

First rollout recommendation:

- reject `task.steps` when `task.container.enabled=true` (explicit validation), or
- document container+steps as unsupported until explicit design is added.

---

## 13. Security

- Do not emit raw secrets in payloads, events, logs, or artifacts.
- Treat step prompts and step skill args as untrusted input.
- Keep existing redaction behavior for token-like values.

---

## 14. Migration Strategy

1. Add optional `task.steps` schema and validation.
2. Update worker prepare to materialize union of referenced step skills.
3. Implement execute-stage step loop and step events/artifacts.
4. Integrate cancellation checks at step boundaries.
5. Add queue submit UI steps editor (preserving publish default `pr`).
6. Add tests for contract normalization, worker flow, cancellation, and UI payload emission.

---

## 15. Open Questions

1. Should blank steps be rejected or treated as explicit "continue" steps?
2. Should failed runs ever allow publish (current guidance: no)?
3. For container tasks, should steps be unsupported or represented as sequential container commands?
4. Should future retry modes support "retry failed step only"?
5. Do we want a future endpoint for appending steps to a running job?
