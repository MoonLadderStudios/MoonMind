# Contract: Agentic Skill Step Authoring

## Valid Direct Task Skill Step

```json
{
  "type": "skill",
  "instructions": "Read MM-564 and implement the selected story.",
  "tool": {
    "type": "skill",
    "name": "moonspec-orchestrate",
    "version": "1.0",
    "inputs": { "issueKey": "MM-564" },
    "requiredCapabilities": ["git"]
  },
  "skill": {
    "id": "moonspec-orchestrate",
    "args": { "issueKey": "MM-564" },
    "requiredCapabilities": ["git"]
  }
}
```

Expected behavior:
- accepted as an executable Skill step;
- remains agentic even though it may reference internal tools or capabilities;
- preserves `MM-564` in Skill args when provided.

## Invalid Skill Args

```json
{
  "type": "skill",
  "instructions": "Implement MM-564.",
  "skill": {
    "id": "moonspec-orchestrate",
    "args": ["not", "an", "object"]
  }
}
```

Expected behavior: rejected before execution because Skill args must be an object.

## Invalid Mixed Payload

```json
{
  "type": "skill",
  "instructions": "Implement MM-564.",
  "tool": {
    "type": "tool",
    "id": "jira.transition_issue",
    "inputs": { "issueKey": "MM-564" }
  }
}
```

Expected behavior: rejected because Skill steps must not include a non-skill Tool payload.
