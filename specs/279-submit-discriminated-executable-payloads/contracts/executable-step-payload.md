# Contract: Executable Step Payload

Source story: `MM-559` Submit discriminated executable payloads.

## Accepted executable submitted steps

```json
{
  "id": "fetch-jira-issue",
  "title": "Fetch Jira issue",
  "type": "tool",
  "tool": {
    "id": "jira.get_issue",
    "version": "1.0.0",
    "inputs": {
      "issueKey": "MM-559"
    }
  },
  "source": {
    "kind": "preset-derived",
    "presetId": "jira.implementation_flow",
    "version": "2026-04-28",
    "originalStepId": "fetch-issue"
  }
}
```

```json
{
  "id": "implement-issue",
  "title": "Implement issue",
  "type": "skill",
  "skill": {
    "id": "moonspec-implement",
    "args": {
      "issueKey": "MM-559"
    }
  }
}
```

## Rejected executable submitted steps

Unresolved Preset step:

```json
{
  "type": "preset",
  "preset": {
    "id": "jira.implementation_flow",
    "inputs": {
      "issueKey": "MM-559"
    }
  }
}
```

Temporal implementation label:

```json
{
  "type": "activity",
  "activity": {
    "name": "mm.skill.execute"
  }
}
```

Conflicting executable payload:

```json
{
  "type": "tool",
  "tool": {
    "id": "jira.get_issue",
    "inputs": {
      "issueKey": "MM-559"
    }
  },
  "skill": {
    "id": "moonspec-implement",
    "args": {}
  }
}
```

## Runtime mapping

| Submitted Step Type | Runtime materialization |
| --- | --- |
| `tool` | Plan node invoking the typed tool id/name from `tool` |
| `skill` | Plan node invoking the selected agent-facing skill |
| `preset` | No runtime node; rejected before materialization |
| `activity` | No runtime node; rejected before materialization |
