# Data Model: Jules Question Auto-Answer

## New Pydantic Models

### JulesAgentMessage

Maps the `AgentMessaged` activity type from the Jules Activities API.

| Field | Type | Description |
|-------|------|-------------|
| `agent_message` | `str` | The message text Jules posted (the question) |

### JulesActivity

A single activity from a Jules session. Activity type is a union field.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Full resource name (e.g., `sessions/{session}/activities/{id}`) |
| `id` | `str` | Activity ID |
| `description` | `Optional[str]` | Activity description |
| `create_time` | `Optional[str]` | ISO 8601 creation timestamp |
| `originator` | `Optional[str]` | Entity that originated the activity (`agent`, `user`, `system`) |
| `agent_messaged` | `Optional[JulesAgentMessage]` | Present when activity type is `AgentMessaged` |

### JulesListActivitiesResult

Result from the `integration.jules.list_activities` activity.

| Field | Type | Description |
|-------|------|-------------|
| `latest_agent_question` | `Optional[str]` | Extracted question text from the most recent `AgentMessaged` activity |
| `activity_id` | `Optional[str]` | Activity ID for deduplication |
| `session_id` | `str` | The session ID queried |

## Modified Types

### JulesNormalizedStatus

Add `"awaiting_feedback"` to the literal type:

```python
JulesNormalizedStatus = Literal[
    "queued", "running", "succeeded", "failed", "canceled", "unknown",
    "awaiting_feedback",  # NEW
]
```

## Workflow State (In-Memory)

### AutoAnswerState (conceptual — workflow variables, not a model)

| Variable | Type | Description |
|----------|------|-------------|
| `_answered_activity_ids` | `set[str]` | Activity IDs already answered for deduplication |
| `_auto_answer_count` | `int` | Number of auto-answer cycles completed in this run |
