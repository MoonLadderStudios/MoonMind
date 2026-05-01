# Contract: Governed Tool Authoring UI

## Trusted Tool Discovery

Request:

```http
GET /mcp/tools
```

Expected response shape:

```json
{
  "tools": [
    {
      "name": "jira.transition_issue",
      "description": "Transition a Jira issue.",
      "inputSchema": { "type": "object" }
    }
  ]
}
```

UI obligations:
- Render discovered tools in grouped choices.
- Allow search by group, name, and description.
- Selecting a discovered tool populates the Tool id.
- Discovery failure leaves manual Tool id/version/inputs fields available.

## Dynamic Jira Transitions

Request:

```http
POST /mcp/tools/call
Content-Type: application/json

{
  "tool": "jira.get_transitions",
  "arguments": {
    "issueKey": "MM-576",
    "expandFields": true
  }
}
```

Expected response shape:

```json
{
  "result": {
    "transitions": [
      { "id": "31", "name": "In Progress", "to": { "name": "In Progress" } }
    ]
  }
}
```

UI obligations:
- Do not request transitions without an issue key.
- Do not guess statuses or transition IDs.
- Selecting a returned status updates Tool inputs JSON with `targetStatus`.
- Transition-option failure is visible and does not block manual JSON editing.
