# Task Steps System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  

## 1. Purpose

The Task Steps system enables a single `MoonMind.Run` workflow execution to navigate an ordered sequence of programmatic steps. 

It accomplishes:
- One Execution Workflow (`MoonMind.Run`).
- Common top-level context (Runtime, Workspace bounds, Repository).
- Step-specific iteration for distinct skills or distinct prompts.

## 2. Canonical Payload Contract Extension

The payload accepts an optional `task.steps` array. 

```json
{
  "task": {
    "instructions": "Overall objective",
    "steps": [
      {
        "id": "step-1",
        "title": "Analyze the problem",
        "instructions": "Optional step-specific instructions"
      },
      {
        "id": "step-2",
        "skill": { "id": "bash_executer" }
      }
    ]
  }
}
```

If omitted, it behaves as an implicit single-step process.

## 3. Temporal Execution Loop

The Temporal `MoonMind.Run` implementation explicitly cycles through these steps via its Workflow code.

1. It resolves the full step list.
2. For each step, it executes an Activity (e.g., standard `vision.generate_context` or an LLM call).
3. It emits explicit `task.step.started` and `task.step.finished` events to the Event stream.
4. If a step errors, the Workflow gracefully intercepts it and propagates the failure to the remainder of the routine.
5. All steps stream their output into scoped blocks on the Mission Control Terminal widget.
