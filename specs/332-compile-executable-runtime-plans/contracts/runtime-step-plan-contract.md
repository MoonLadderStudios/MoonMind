# Contract: Runtime Step Plan Compilation

## Scope

This contract covers MM-573 runtime payload and proposal promotion boundaries. It preserves target Jira issue `MM-573`, source issue `manual-mm-569-mm-574`, and the original Jira preset brief as traceability inputs.

## Executable Task Payload

Executable payloads submitted to runtime planning must use flattened step entries:

```json
{
  "task": {
    "steps": [
      {
        "id": "fetch-issue",
        "type": "tool",
        "instructions": "Fetch the Jira issue.",
        "tool": {
          "id": "jira.get_issue",
          "version": "1.0.0",
          "inputs": {"issueKey": "MM-573"}
        },
        "source": {
          "kind": "preset-derived",
          "presetSlug": "jira-orchestrate",
          "presetVersion": "1.0.0"
        }
      },
      {
        "id": "implement-story",
        "type": "skill",
        "instructions": "Implement the selected story.",
        "skill": {
          "id": "moonspec-implement",
          "version": "1.0.0",
          "inputs": {}
        }
      }
    ]
  }
}
```

Rules:

- `task.steps[].type` must be `tool` or `skill` at executable runtime boundaries.
- Tool steps require `tool.id` or `tool.name` and may carry versioned inputs.
- Skill steps require an executable skill selector.
- `source` and `authoredPresets` are audit metadata, not live runtime catalog lookups.
- `type: "preset"`, include nodes, or unresolved preset placeholders are rejected before runtime execution.

## Runtime Plan Node Output

Planning converts each executable step to a runtime node:

```json
{
  "nodes": [
    {
      "id": "fetch-issue",
      "tool": {
        "type": "skill",
        "name": "jira.get_issue",
        "version": "1.0.0"
      },
      "inputs": {
        "instructions": "Fetch the Jira issue.",
        "selectedSkill": "jira.get_issue",
        "type": "tool",
        "issueKey": "MM-573"
      }
    }
  ],
  "edges": []
}
```

Rules:

- Tool steps map to typed tool invocation plan nodes.
- Skill steps map to plan nodes, skill activities, child workflows, or managed-session requests as selected by runtime policy.
- Step order is preserved with sequential edges for multi-step plans.
- Unsupported runtime node executor types fail explicitly.

## Proposal Promotion Boundary

Promotion from reviewed proposal to execution must use the stored reviewed task payload:

```json
{
  "task": {
    "authoredPresets": [{"presetId": "runtime-quality-followup"}],
    "steps": [
      {
        "id": "step-1",
        "type": "skill",
        "instructions": "Run reviewed work.",
        "skill": {"id": "auto"},
        "source": {"kind": "preset-derived"}
      }
    ]
  }
}
```

Rules:

- Promotion validates the reviewed flat payload.
- Promotion preserves preset provenance.
- Promotion rejects unresolved `type: "preset"` steps.
- Promotion must not call live preset expansion or silently refresh to the latest catalog definition.

## Required Verification

- Unit: task contract rejects unresolved Preset/include steps.
- Unit: runtime planner maps Tool and Skill steps to expected plan nodes/materialization inputs.
- Unit: proposal promotion preserves reviewed flattened payload and does not live re-expand.
- Integration: task-shaped submission boundary preserves flattened steps and provenance if API/execution boundary behavior changes.
