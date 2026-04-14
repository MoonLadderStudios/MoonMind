---
name: jira-issue-updater
description: Update Jira issues such as tasks, stories, bugs, or subtasks through MoonMind's trusted Jira tool surface. Use when a user asks to edit Jira fields, move workflow status, update descriptions, or publish completion/status summaries back to any Jira issue type.
---

# Jira Issue Updater

Update an existing Jira task, story, bug, subtask, or other issue type. Use this skill when the request requires changing an issue's workflow status, description, or editable fields. Prefer MoonMind's trusted Jira tools over direct Jira REST calls so credentials stay outside the agent runtime.

## Inputs

- Required: Jira issue key, for example `ENG-123`.
- Optional: target status or workflow state, for example `Done`, `In Review`, or `Blocked`.
- Optional: replacement or appended description text.
- Optional: issue type context, for example `Task`, `Story`, `Bug`, `Sub-task`, or a project-specific Jira issue type.
- Optional: field/update payload details from the user or Jira edit metadata, including labels, priority, assignee, components, due dates, or project-specific custom fields.
- Optional: MoonMind API base URL and authenticated session/token, only when calling the HTTP MCP endpoint directly.

## Outputs

- Confirmation of the issue key updated.
- Description update result when `jira.edit_issue` is used.
- Transition result when `jira.transition_issue` is used.
- Field update result when editable issue fields are changed.
- Any skipped action with a short reason.

## Workflow

1. Validate the requested issue key and scope.
   - Use a concrete Jira issue key; do not infer one from unrelated text unless the user clearly identified it.
   - Treat the issue key, not the issue type, as the update target. The same workflow applies to tasks, stories, bugs, subtasks, and custom issue types unless Jira metadata or policy blocks the requested field.
   - If the user asks for a status change by name, treat that name as the desired end state, not as a transition ID.
   - If the request references a parent or related issue, distinguish it from the issue being updated before mutating anything.

2. Discover available tools and issue state.
   - If runtime-native Jira tools are available, use them directly.
   - Otherwise, if the MoonMind API is reachable and authenticated, call `GET /mcp/tools` and `POST /mcp/tools/call`.
   - Confirm only the tools needed for the requested action are registered before relying on them.
   - For description updates, require `jira.edit_issue`; also require `jira.get_issue` when preserving current description text for an append, and `jira.get_edit_metadata` when editable field shape is unclear.
   - For general field updates, require `jira.edit_issue` and use `jira.get_edit_metadata` to map requested field names to Jira's editable schema instead of hardcoding custom field IDs.
   - For status updates, require `jira.get_transitions` and `jira.transition_issue`; use `jira.get_issue` when current status or final verification is needed.
   - If one requested action is unavailable because its tools are disabled by policy, skip that action with a reason instead of blocking other requested actions whose tools are available.
   - Fetch the issue with `jira.get_issue` when current fields or status are needed.

3. Update the description when requested.
   - Use `jira.get_edit_metadata` first if the editable field shape is unclear.
   - Use `jira.edit_issue` with the `description` field for replacement updates.
   - Preserve existing description text when the user asks to append; fetch the current description first and build the combined value.
   - Use Atlassian Document Format (ADF) for the `description` field when calling `jira.edit_issue` on Jira Cloud; the tool passes fields through and does not convert plain text for edit actions.
   - Send plain text only when Jira metadata or local code confirms the edit action accepts plain text for the target field.

Example tool payload:

```json
{
  "tool": "jira.edit_issue",
  "arguments": {
    "issueKey": "ENG-123",
    "fields": {
      "description": {
        "type": "doc",
        "version": 1,
        "content": [
          {
            "type": "paragraph",
            "content": [
              {
                "type": "text",
                "text": "Updated implementation notes..."
              }
            ]
          }
        ]
      }
    }
  }
}
```

4. Update other editable fields when requested.
   - Use `jira.get_edit_metadata` to identify editable fields and required shapes for the target issue.
   - Map human names such as assignee, priority, labels, components, sprint, fix versions, due date, and custom fields through metadata or connector-provided field definitions.
   - Send only fields the user requested and Jira allows for the selected issue.
   - Do not invent values for issue type-specific fields. If a required value is missing, stop with the exact missing field instead of guessing.

Example tool payload:

```json
{
  "tool": "jira.edit_issue",
  "arguments": {
    "issueKey": "ENG-123",
    "fields": {
      "labels": ["release-readiness"],
      "priority": {
        "name": "High"
      }
    }
  }
}
```

5. Change status when requested.
   - Call `jira.get_transitions` for the issue before transitioning.
   - Match the user's requested status against available transition names or target statuses.
   - Use the transition ID returned by Jira; do not guess IDs.
   - If multiple transitions could match, choose only when the user's intent is unambiguous; otherwise stop with the available options.
   - Use `jira.transition_issue` and include required transition fields from expanded transition metadata when present.

Example tool payload:

```json
{
  "tool": "jira.transition_issue",
  "arguments": {
    "issueKey": "ENG-123",
    "transitionId": "31"
  }
}
```

6. Verify and report.
   - Re-fetch the issue when possible to confirm the final status or description.
   - Report only sanitized results: issue key, issue type when known, final status, changed fields, and tool result IDs.
   - Never print auth headers, SecretRefs resolved to plaintext, API tokens, cookies, or full environment dumps.

## HTTP MCP Invocation

When no native Jira tool is exposed but an authenticated MoonMind API endpoint is available, call:

```bash
curl -sS -X POST "$MOONMIND_API_BASE/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "jira.get_transitions",
    "arguments": {
      "issueKey": "ENG-123",
      "expandFields": true
    }
  }'
```

Use the authentication mechanism already provided by the runtime or user. Do not ask the user to paste raw Atlassian credentials into the shell, task prompt, artifacts, or logs.

## Failure Modes

- Jira tool not registered: report that Jira tools are disabled or unavailable in this runtime.
- `jira_not_configured`: report that MoonMind Jira credentials/configuration are missing.
- `jira_policy_denied`: report that the requested project or action is outside configured policy.
- `jira_validation_failed`: re-check issue key, editable fields, required transition fields, and transition ID.
- Unsupported issue type or field: explain which value is unsupported for the selected issue and which metadata check blocked it.
- `jira_auth_failed` or HTTP 401/403: report an auth or permission problem without exposing secrets.
- Rate limiting or transient Jira failure: retry only through the trusted tool's normal retry behavior; do not build an ad hoc raw Jira client.

## Guardrails

- Do not call Jira directly with raw `ATLASSIAN_API_KEY` or other credentials from the agent shell.
- Do not mutate status via `jira.edit_issue`; use `jira.transition_issue`.
- Do not use a generic raw HTTP tool for Jira mutations when the trusted Jira tools exist.
- Do not silently overwrite descriptions when the user requested an append or status-only update.
- Do not claim Jira was updated unless the tool call succeeded.
