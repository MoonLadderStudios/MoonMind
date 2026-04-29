# Contract: Executable Step Validation

## Accepted Tool Step

```json
{
  "type": "tool",
  "title": "Move Jira issue to In Progress",
  "instructions": "Move MM-557 to In Progress after blocker checks pass.",
  "tool": {
    "id": "jira.transition_issue",
    "version": "1.0.0",
    "inputs": {
      "issueKey": "MM-557",
      "targetStatus": "In Progress"
    },
    "requiredAuthorization": "jira",
    "requiredCapabilities": ["jira"],
    "sideEffectPolicy": "idempotent-by-transition-target"
  }
}
```

Expected result: accepted and persisted/expanded with `type: tool` and normalized `tool.inputs`.

## Accepted Skill Step

```json
{
  "type": "skill",
  "title": "Triage Jira issue",
  "instructions": "Read MM-557 and decide whether it needs clarification.",
  "skill": {
    "id": "jira-triage",
    "args": {"issueKey": "MM-557"},
    "requiredCapabilities": ["jira"],
    "autonomy": {"mode": "bounded"}
  }
}
```

Expected result: accepted and persisted/expanded with `type: skill` and Skill metadata.

## Rejected Mixed Step

```json
{
  "type": "tool",
  "instructions": "Implement MM-557.",
  "skill": {"id": "jira-implement", "args": {"issueKey": "MM-557"}}
}
```

Expected result: validation error before persistence or expansion.

## Rejected Shell Step

```json
{
  "type": "skill",
  "instructions": "Run deployment script.",
  "command": "bash deploy.sh"
}
```

Expected result: validation error before persistence or expansion.
