# Task Dependencies System

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskCancellation.md`, `docs/Tasks/TaskProposalSystem.md`, `docs/Temporal/TemporalArchitecture.md`

---

## 1. Purpose

This document outlines a task-dependencies system that lets one task wait for
other tasks to complete successfully before work begins.

Dependencies are modeled between separate `MoonMind.Run` executions.

---

## 2. Requirements

- a task may declare up to 10 prerequisite task IDs
- prerequisite IDs are Temporal workflow IDs
- dependent tasks remain in `waiting_on_dependencies` until prerequisites
  succeed
- failed or canceled prerequisites fail the dependent task
- Mission Control should surface dependency state in create, list, and detail
  flows

---

## 3. Backend component

### 3.1 Payload shape

The canonical task payload accepted through `POST /api/executions` includes an
optional `dependsOn` field:

```json
{
  "task": {
    "instructions": "Run integration tests",
    "dependsOn": [
      "mm:task-run-id-1",
      "mm:task-run-id-2"
    ]
  }
}
```

Rules:

- `dependsOn` entries are Temporal workflow IDs
- for task views, `taskId == workflowId`
- the API validates each dependency before accepting the create request

### 3.2 Workflow behavior

`MoonMind.Run` enforces dependencies before planning begins.

Lifecycle:

1. `initializing`
2. `waiting_on_dependencies`
3. `planning`
4. `executing`
5. `proposals`
6. `finalizing`
7. terminal state

Preferred wait mechanism:

- use Temporal external workflow handles
- wait on dependency completion directly
- fail fast if a dependency fails or is canceled

### 3.3 Validation

The API must validate:

1. each dependency exists
2. each dependency is a `MoonMind.Run` workflow
3. no self-dependency
4. the dependency count limit
5. no cycles

---

## 4. Frontend component

### 4.1 Task creation and editing

- add a Dependencies section to the create/edit UI
- use a multi-select or typeahead backed by the task list APIs
- enforce the 10-dependency limit client-side

### 4.2 List and detail views

- show a `waiting_on_dependencies` badge/state
- show blocking task titles or summaries
- link directly to prerequisite task detail views
- optionally show reverse dependencies in the future

---

## 5. Failure modes

- missing dependency -> fail immediately
- unsuccessful dependency -> fail dependent task
- workflow timeout -> normal workflow timeout semantics apply
- dependency wait across Continue-As-New -> preserved by durable workflow input

---

## 6. Implementation tracking

Open work is tracked in:

- `docs/tmp/remaining-work/Tasks-TaskDependencies.md`
- `docs/tmp/remaining-work/plans-TaskDependenciesPlan.md`
