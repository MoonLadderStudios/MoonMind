# Contract: Governed Tool Picker

## Tool Metadata Loading

The Create page loads trusted Tool metadata from the MoonMind API before or during Tool step authoring.

Expected response shape:

```json
{
  "tools": [
    {
      "name": "jira.transition_issue",
      "description": "Apply an explicit Jira workflow transition.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "issueKey": { "type": "string" },
          "transitionId": { "type": "string" }
        },
        "required": ["issueKey", "transitionId"]
      }
    }
  ]
}
```

Authoring obligations:
- Tool choices are grouped by integration or domain.
- Search matches Tool id, group, description, and schema field names.
- Selecting a Tool binds the draft to that Tool id.
- Unknown Tool ids are not silently accepted by governed authoring.
- Required input fields from `inputSchema.required` are visible or represented in validation feedback.

## Schema-Guided Tool Inputs

For a selected Tool, the authoring surface shows relevant schema fields and preserves the submitted payload under:

```json
{
  "type": "tool",
  "tool": {
    "type": "tool",
    "id": "jira.transition_issue",
    "version": "1.0.0",
    "inputs": {
      "issueKey": "MM-576",
      "transitionId": "31"
    }
  }
}
```

Rules:
- `tool.id` is required and must come from trusted metadata when governed catalog metadata is available.
- `tool.version` is optional only when the runtime can resolve the selected Tool version.
- `tool.inputs` must be an object.
- Required schema fields must be present before submission when the authoring surface can evaluate them.
- Step-level `command`, `cmd`, `script`, `shell`, and `bash` keys are rejected before execution.
- `skill` is omitted for Tool steps.

## Dynamic Option Provider: Jira Target Status

When the selected Tool is a Jira transition operation and an issue key is available, the authoring surface requests valid transitions through the trusted Jira tool path.

Trusted call shape:

```json
{
  "tool": "jira.get_transitions",
  "arguments": {
    "issueKey": "MM-576",
    "expandFields": true
  }
}
```

Expected option derivation:
- Each returned transition may produce a target-status option from `to.name` and retain the transition id internally when the submitted Tool requires `transitionId`.
- If options cannot be fetched, stale or free-text target status submission is blocked.
- The UI must not guess transition ids.
- Provider errors remain visible before submission.
- Only trusted MoonMind tool calls are used for provider-backed options.
