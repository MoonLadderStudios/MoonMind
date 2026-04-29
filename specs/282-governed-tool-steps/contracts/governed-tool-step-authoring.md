# Contract: Governed Tool Step Authoring

## Create-Page Submission Payload

When a manual step has Step Type `Tool`, the submitted task step MUST use:

```json
{
  "type": "tool",
  "instructions": "Fetch the issue before implementation.",
  "tool": {
    "type": "tool",
    "id": "jira.get_issue",
    "version": "1.0",
    "inputs": {
      "issueKey": "MM-563"
    }
  }
}
```

Rules:
- `tool.id` is required.
- `tool.version` is omitted when blank.
- `tool.inputs` is always an object.
- `skill` is omitted for Tool steps.
- `command`, `cmd`, `script`, `shell`, and `bash` are forbidden executable step keys.
