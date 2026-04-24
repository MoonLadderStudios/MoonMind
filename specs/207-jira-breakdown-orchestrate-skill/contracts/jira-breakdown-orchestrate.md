# Contract: Jira Breakdown and Orchestrate

## Seeded Preset Contract

The system exposes a global reusable preset or equivalent skill surface named for Jira Breakdown and Orchestrate.

Required behavior:

1. Accept a source feature request or Jira-derived brief.
2. Run the normal Jira Breakdown workflow.
3. Create Jira story issues from the generated breakdown using the existing trusted Jira story-output path.
4. Create one downstream Jira Orchestrate task for each created or reused Jira story issue.
5. Create downstream task dependencies so each later task waits for the immediately earlier task.
6. Return a structured result describing created tasks, dependencies, skipped stories, failures, and traceability.

The composite surface must not:

- replace the existing `jira-breakdown` preset,
- replace the existing `jira-orchestrate` preset,
- run downstream story implementation inline inside the breakdown step,
- call Jira with raw credentials from an agent runtime.

## Downstream Task Creation Tool Contract

Suggested tool name: `story.create_jira_orchestrate_tasks`

Input shape:

```json
{
  "jira": {
    "issueMappings": [
      {
        "storyId": "STORY-001",
        "storyIndex": 1,
        "summary": "First story",
        "issueKey": "MM-501"
      }
    ]
  },
  "task": {
    "repository": "MoonLadderStudios/MoonMind",
    "runtime": {
      "mode": "codex"
    },
    "publish": {
      "mode": "none"
    },
    "orchestrationMode": "runtime"
  },
  "traceability": {
    "sourceIssueKey": "MM-404",
    "sourceBriefRef": "spec.md (Input)"
  }
}
```

Output shape:

```json
{
  "status": "completed",
  "storyCount": 3,
  "createdTaskCount": 3,
  "dependencyCount": 2,
  "tasks": [
    {
      "storyId": "STORY-001",
      "storyIndex": 1,
      "jiraIssueKey": "MM-501",
      "workflowId": "mm:...",
      "dependsOn": []
    },
    {
      "storyId": "STORY-002",
      "storyIndex": 2,
      "jiraIssueKey": "MM-502",
      "workflowId": "mm:...",
      "dependsOn": ["mm:..."]
    }
  ],
  "dependencies": [
    {
      "fromStoryId": "STORY-001",
      "toStoryId": "STORY-002",
      "status": "created"
    }
  ],
  "failures": [],
  "traceability": {
    "sourceIssueKey": "MM-404"
  }
}
```

## Validation Rules

- Input issue mappings are sorted by `storyIndex` before task creation.
- A mapping without `issueKey` is skipped with a failure entry.
- The first downstream task has no task dependency.
- Each later task has exactly one direct dependency: the previous created downstream task's `workflowId`.
- Dependency wiring uses the existing task create contract, not template-authored dependency graphs.
- Stable idempotency keys are used so retries do not duplicate downstream tasks.
- Partial success must be reported as `partial`, not `completed`.
- Zero created downstream tasks must be reported as `no_downstream_tasks` or `failed`, depending on whether the absence is valid or erroneous.

## Jira Orchestrate Task Payload Requirements

Each downstream task must include:

- the generated story Jira issue key,
- instructions to run Jira Orchestrate for that issue,
- runtime mode and publish behavior from the composite request or safe defaults,
- repository target when required by downstream implementation,
- MM-404 traceability,
- the original MM-404 brief reference or summary.

## Test Contract

Required unit scenarios:

- three issue mappings create three tasks and two dependency edges,
- one issue mapping creates one task and zero dependency edges,
- zero issue mappings creates zero tasks and a no-downstream-task outcome,
- missing issue key is reported per story,
- failure on task N reports prior successes and does not claim a complete chain,
- dependency validation failure reports the affected stories and workflow IDs,
- idempotency keys are stable across retries.

Required integration scenario:

- startup seed synchronization persists the new composite preset with expected steps and trusted-tool instructions.
