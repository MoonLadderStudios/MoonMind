# Task Steps System

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-02-16

## 1. Purpose

Introduce a **Task Steps** layer for MoonMind Tasks so a single Task run can execute an ordered sequence of **steps**, where each step is executed as though it were a separate queued “message” to the chosen runtime (Codex / Gemini / Claude), while preserving:

- One Task = one queue job (single claim, single workspace, single publish decision)
- One set of task-level controls (runtime, model, effort, repo, branches, publish) applied to the entire run
- Step-level optional overrides for:
  - `instructions` (optional)
  - `skill` selection (optional)

This document is declarative: it defines steady-state contracts, invariants, and expected execution semantics.

---

## 2. Definitions

**Task**
A user-submitted unit of work represented as a single `type="task"` Agent Queue job.

**Step**
An ordered execution unit within a Task. Each step results in **one runtime invocation** (“one message”) executed against the same job workspace.

**Task Run**
The full lifecycle of a Task job from claim → prepare → execute (all steps) → publish (optional) → complete/fail.

**Step Execution**
One runtime invocation (Codex CLI, Gemini CLI, Claude Code, etc.) with a step-specific prompt assembled by MoonMind.

---

## 3. Goals and Non-Goals

### Goals
1. Allow a Task to execute **N steps sequentially** in a single worker claim/session.
2. Each step may optionally provide:
   - step-specific instructions
   - step-specific skill selection
3. Task-level settings apply to the whole run:
   - `repository`, `git.startingBranch`, `git.newBranch`
   - `runtime.mode`, `runtime.model`, `runtime.effort`
   - `publish.mode`, PR settings, etc.
4. Preserve the wrapper stage model:
   - Prepare stage sets up workspace + branch
   - Execute stage runs all steps
   - Publish stage commits/pushes/PR (if enabled)
5. Provide clear observability:
   - job events show step start/finish/failure
   - artifacts include per-step logs and optional per-step patches

### Non-Goals (for this document)
- Live “edit steps while running” interactive continuation (future work)
- Parallel step execution
- Step-level publish/commit (publish is task-level only)
- Replacing orchestrator plans; this is specific to queue Tasks

---

## 4. High-Level Architecture

### 4.1 One Task Job, Many Steps
A Task remains a single Agent Queue job. The worker claims the job once and executes the full step sequence in-order.

This preserves “same device” semantics naturally: all steps run on the worker that holds the lease.

### 4.2 Wrapper Stages Remain the Outer Contract
The Task still executes as:

1. `moonmind.task.prepare`
2. `moonmind.task.execute` (runs Step 1..N)
3. `moonmind.task.publish` (conditional / may skip)

Steps are **internal** to the execute stage.

---

## 5. Canonical Payload Contract Extension

### 5.1 Add `task.steps[]` (Optional)
Extend the canonical Task execution object with an optional `steps` array.

If `steps` is omitted or empty, the Task behaves exactly as today: it is treated as a single-step Task derived from `task.instructions` and `task.skill`.

### 5.2 Step Schema

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
````

Field rules:

* `id` (optional): UI-generated stable identifier for referencing events/artifacts. If omitted, the worker uses `index`-based ids.
* `title` (optional): UI-only convenience label (may be included in payload for display).
* `instructions` (optional): step directive text. If missing, the step inherits the task objective only.
* `skill` (optional):

  * If missing, the step inherits task-level `task.skill`.
  * If present, it overrides only for this step.
* Step-level runtime/model/effort/repo/publish are not allowed. All of those remain task-level.

### 5.3 Task-Level Instructions Still Required

`task.instructions` remains the required “Task Objective” and applies to the entire run.

Steps optionally add or refine instructions per message.

### 5.4 Backward Compatibility

* Existing payloads without `task.steps` continue to work unchanged.
* The normalization layer may materialize an implicit single step at execution time for convenience, but MUST NOT require producers to send steps.

---

## 6. Step Prompt Assembly

Each step produces one runtime invocation prompt assembled as:

**Effective Skill**

* `step.skill.id` if present else `task.skill.id` else `auto`

**Effective Instructions**

* Always include the **Task Objective** (`task.instructions`)
* If `step.instructions` is present, include it as **Step Instructions**
* If `step.instructions` is missing, the prompt uses Task Objective plus a deterministic “continue” header

### 6.1 Deterministic Prompt Template (Runtime-Neutral)

```
MOONMIND TASK OBJECTIVE:
{task.instructions}

STEP {i+1}/{N} {stepIdOrIndex} {optionalTitle}:
{step.instructions or "(no step-specific instructions; continue based on objective)"}

EFFECTIVE SKILL:
{effectiveSkillId}

WORKSPACE:
- Repo is checked out and on the effective working branch already.
- Do NOT commit or push. Publish is handled by MoonMind publish stage.
- Skills are available under: .agents/skills/ and .gemini/skills/ (symlinks to job skills workspace).
- Write logs to stdout/stderr; MoonMind captures them.
```

If `effectiveSkillId != "auto"`, append:

```
SKILL USAGE:
Use the selected skill's files under .agents/skills/{skill}/ as the procedure for this step.
```

---

## 7. Worker Execution Semantics

### 7.1 Prepare Stage (Unchanged Outer Purpose; Extended Skill Materialization)

Prepare stage responsibilities remain:

* Create job workspace directories (`repo/`, `home/`, `skills_active/`, `artifacts/`)
* Clone repo, resolve default branch, compute effective working branch
* Materialize skills and create skill links
* Write `task_context.json`

**Change for TaskSteps:**

* Materialize the **union of all skills referenced** by:

  * `task.skill.id` (if not `auto`)
  * `task.steps[*].skill.id` (if not `auto`)
* If union is empty (all `auto`), still ensure shared skill links exist (as today).

### 7.2 Execute Stage (New: Runs Step Loop)

Execute stage MUST:

1. Resolve the step list:

   * If `task.steps` is present and non-empty → use it
   * Else → create an implicit single step:

     * instructions: null
     * skill: inherit task.skill
2. For each step in order:

   * Emit `task.step.started`
   * Invoke runtime adapter once (one “message”) with assembled prompt
   * Capture stdout/stderr to step log artifacts
   * Optionally compute and store a per-step patch (recommended)
   * Emit `task.step.finished` on success OR `task.step.failed` on failure
3. If a step fails:

   * The execute stage fails immediately (no further steps)
   * Publish stage behavior is governed by task publish mode:

     * Recommended default: **do not publish on execute failure**

### 7.3 Publish Stage (Once per Task Run)

Publish stage occurs once after all steps succeed, using the existing publish mode semantics:

* `none`: skip
* `branch`: commit + push
* `pr`: commit + push + PR

Steps MUST NOT commit/push. The prompt template reinforces this constraint.

---

## 8. Events and Artifacts

### 8.1 New Required Events (Step Timeline)

Workers MUST emit:

* `task.steps.plan` (count + ids)
* `task.step.started`
* `task.step.finished`
* `task.step.failed`

Event payload fields (minimum):

* `stepIndex` (0-based)
* `stepId` (from payload or generated)
* `effectiveSkill` (`auto` or concrete)
* `hasStepInstructions` (bool)
* `summary` (optional small summary string)

### 8.2 Step Artifacts (Recommended)

For each step `i`:

* `logs/steps/step-000{i}.log`
* `patches/steps/step-000{i}.patch` (optional but recommended)

Plus existing task artifacts:

* `logs/prepare.log`
* `logs/execute.log` (may become the aggregate “execute stage” log)
* `logs/publish.log` (when publish enabled or “skipped” is recorded)
* `patches/changes.patch` (final diff snapshot)
* `task_context.json`
* `publish_result.json` (when publish enabled)

---

## 9. Task Dashboard UI Changes

### 9.1 Queue Submit UI: Add Steps Editor

The Queue Submit form (`/tasks/queue/new`) adds a **Steps** section.

UI behavior:

* Task-level fields remain the same:

  * repository, runtime, model, effort, publish mode, branches, etc.
* Add a Steps editor with:

  * “Add Step” button
  * Step list with:

    * optional step instructions textarea
    * optional skill dropdown
    * remove step
    * reorder step up/down (optional but recommended)

### 9.2 Defaults and Ergonomics

* By default, the form shows **one step**.
* The existing required “Instructions” field becomes the **Task Objective** (`task.instructions`).
* Each step initially defaults to:

  * `instructions`: empty (inherits objective)
  * `skill`: empty (inherits task-level skill or defaults to auto)
* Submission validation:

  * `task.instructions` is required (objective)
  * `steps` may be empty; empty means implicit single-step mode
  * If steps exist, the UI SHOULD prevent steps that are completely blank (no instructions and no skill override), but backend may treat them as “continue” steps if present.

### 9.3 Payload Emitted by UI (Example)

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
        },
        {
          "id": "step-3",
          "title": "Implement code + tests",
          "instructions": "Implement the code changes and update unit tests.",
          "skill": { "id": "auto", "args": {} }
        }
      ],
      "git": { "startingBranch": null, "newBranch": null },
      "publish": { "mode": "branch", "prBaseBranch": null, "commitMessage": null, "prTitle": null, "prBody": null }
    }
  }
}
```

Notes:

* UI may omit `requiredCapabilities`; server normalization derives it.
* Model/effort/repo/publish are task-level only (apply to all steps).

### 9.4 Queue Detail UI Enhancements (Recommended)

On `/tasks/queue/:jobId`, render a Step timeline by parsing job events:

* Show N steps, current step index, per-step status
* Link step logs/patches if present

---

## 10. Server and Contract Changes

### 10.1 Task Contract Models

Extend the canonical Task payload validation with:

* `TaskStepSpec`
* `task.steps: list[TaskStepSpec] = []`

### 10.2 Capability Derivation

Update capability derivation to include:

* runtime capability
* `git`
* `gh` when publish mode is `pr`
* union of `requiredCapabilities` declared by:

  * `task.skill.requiredCapabilities`
  * `task.steps[*].skill.requiredCapabilities`

---

## 11. Worker Implementation Notes (Codex First)

### 11.1 Codex Step Execution

Codex adapter will execute each step as one `codex exec ... <prompt>` invocation, in the same repo directory, without re-cloning.

### 11.2 Skill Handling Per Step

If `effectiveSkillId != auto`, execute step via the skill path (skill-first) for that step:

* Materialize all step skills during prepare
* Invoke the skill-compatible handler per step
* Record step-level execution metadata in step events

---

## 12. Security

* No raw secrets in payloads, events, or artifacts.
* Step prompts must not include secrets.
* Skill args are treated as untrusted; redact token-like strings from logs/artifacts (existing redaction applies).

---

## 13. Migration Strategy

1. Add `task.steps` as optional, keeping existing `task.instructions` and `task.skill`.
2. Update worker to:

   * materialize union of skills
   * iterate steps in execute stage
3. Update dashboard submit UI to support adding steps.
4. Update docs and tests.

---

## 14. Open Questions

1. Should a completely blank step be treated as a no-op, or rejected?
2. Do we want step-level retry (re-run only the failed step) in the future?
3. Should publish stage be allowed to run when steps fail (default: no)?
4. Do we want a future “interactive add steps while running” endpoint to append steps to a running job?

```

Key implementation alignment notes (why this fits MoonMind today):
- Queue job creation already normalizes payloads server-side (so adding `task.steps` is safe and doesn’t require producers to perfectly compute `requiredCapabilities`) :contentReference[oaicite:3]{index=3}.
- Current capability derivation includes runtime + `git` and adds `gh` for PR publishing; the steps system extends this by unioning step-skill capabilities :contentReference[oaicite:4]{index=4}.
- Current worker prepare stage materializes only one selected skill when `selected_skill != "auto"`; TaskSteps requires materializing the union of step skills instead :contentReference[oaicite:5]{index=5}.
