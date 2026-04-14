---
name: jira-task-update
description: Update Jira task status and/or description through MoonMind's trusted Jira tool surface. Use when a user asks to move a Jira issue, change workflow status, edit an issue description, or publish a task completion/status summary back to Jira.
---

# Jira Task Update

Use this skill when the task requires changing an existing Jira issue's workflow status, description, or both. Prefer MoonMind's trusted Jira tools over direct Jira REST calls so credentials stay outside the agent runtime.

## Inputs

- Required: Jira issue key, for example `ENG-123`.
- Optional: target status or workflow state, for example `Done`, `In Review`, or `Blocked`.
- Optional: replacement or appended description text.
- Optional: field/update payload details from the user or Jira edit metadata.
- Optional: MoonMind API base URL and authenticated session/token, only when calling the HTTP MCP endpoint directly.

## Outputs

- Confirmation of the issue key updated.
- Description update result when `jira.edit_issue` is used.
- Transition result when `jira.transition_issue` is used.
- Any skipped action with a short reason.

## Workflow

1. Validate the requested issue key and scope.
   - Use a concrete Jira issue key; do not infer one from unrelated text unless the user clearly identified it.
   - If the user asks for a status change by name, treat that name as the desired end state, not as a transition ID.

2. Discover available tools and issue state.
   - If runtime-native Jira tools are available, use them directly.
   - Otherwise, if the MoonMind API is reachable and authenticated, call `GET /mcp/tools` and `POST /mcp/tools/call`.
   - Confirm only the tools needed for the requested action are registered before relying on them.
   - For description updates, require `jira.edit_issue`; also require `jira.get_issue` when preserving current description text for an append, and `jira.get_edit_metadata` when editable field shape is unclear.
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

4. Change status when requested.
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

5. Verify and report.
   - Re-fetch the issue when possible to confirm the final status or description.
   - Report only sanitized results: issue key, final status, changed fields, and tool result IDs.
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
- `jira_auth_failed` or HTTP 401/403: report an auth or permission problem without exposing secrets.
- Rate limiting or transient Jira failure: retry only through the trusted tool's normal retry behavior; do not build an ad hoc raw Jira client.

## Guardrails

- Do not call Jira directly with raw `ATLASSIAN_API_KEY` or other credentials from the agent shell.
- Do not mutate status via `jira.edit_issue`; use `jira.transition_issue`.
- Do not use a generic raw HTTP tool for Jira mutations when the trusted Jira tools exist.
- Do not silently overwrite descriptions when the user requested an append or status-only update.
- Do not claim Jira was updated unless the tool call succeeded.
