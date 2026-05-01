# Contract: Agentic Skill Step Authoring

## Valid Direct Task Skill Step

Coverage: FR-001, FR-002, FR-004, FR-005, FR-007, FR-008, SC-001, DESIGN-REQ-009, DESIGN-REQ-010.

```json
{
  "type": "skill",
  "instructions": "Implement MM-577 with the agentic Skill workflow.",
  "tool": {
    "type": "skill",
    "name": "moonspec-orchestrate",
    "version": "1.0",
    "inputs": { "issueKey": "MM-577", "mode": "runtime" },
    "requiredCapabilities": ["git", "jira"]
  },
  "skill": {
    "id": "moonspec-orchestrate",
    "args": { "issueKey": "MM-577", "mode": "runtime" },
    "requiredCapabilities": ["git", "jira"]
  }
}
```

Expected behavior:
- accepted as an executable Skill step;
- remains agentic even though it may reference internal tools or capabilities;
- preserves `MM-577` in Skill args when provided.

## Invalid Skill Args

Coverage: FR-003, FR-005, SC-002, DESIGN-REQ-019.

```json
{
  "type": "skill",
  "instructions": "Implement MM-577.",
  "skill": {
    "id": "moonspec-orchestrate",
    "args": ["not", "an", "object"]
  }
}
```

Expected behavior: rejected before execution because Skill args must be an object.

## Invalid Mixed Payload

Coverage: FR-006, SC-002, DESIGN-REQ-009, DESIGN-REQ-019.

```json
{
  "type": "skill",
  "instructions": "Implement MM-577.",
  "tool": {
    "type": "tool",
    "id": "jira.transition_issue",
    "inputs": { "issueKey": "MM-577" }
  }
}
```

Expected behavior: rejected because Skill steps must not include a non-skill Tool payload.
