# Contract: Claude Context Snapshots

Source story: MM-345 / STORY-004.

## Python Schema Surface

The managed-session schema boundary must export the following names from `moonmind.schemas.managed_session_models` and `moonmind.schemas`:

- `CLAUDE_CONTEXT_STARTUP_KINDS`
- `CLAUDE_CONTEXT_ON_DEMAND_KINDS`
- `CLAUDE_CONTEXT_EVENT_NAMES`
- `ClaudeContextEvent`
- `ClaudeContextEventName`
- `ClaudeContextGuidanceRole`
- `ClaudeContextLoadedAt`
- `ClaudeContextReinjectionPolicy`
- `ClaudeContextSegment`
- `ClaudeContextSnapshot`
- `ClaudeContextSourceKind`
- `ClaudeContextCompactionResult`
- `claude_default_reinjection_policy`
- `compact_claude_context_snapshot`

## Context Segment Wire Shape

```json
{
  "segmentId": "segment-managed-claude-md",
  "kind": "managed_claude_md",
  "sourceRef": "artifact://context/managed-claude-md",
  "loadedAt": "startup",
  "reinjectionPolicy": "always",
  "guidanceRole": "guidance",
  "tokenBudgetHint": 2048,
  "metadata": {
    "scope": "managed"
  }
}
```

Contract rules:
- `segmentId` and `sourceRef` are required and non-blank.
- `kind`, `loadedAt`, `reinjectionPolicy`, and `guidanceRole` reject unknown values.
- CLAUDE guidance and memory source kinds reject `guidanceRole = "enforcement"`.
- `metadata` must remain compact and must not carry full source payloads.

## Context Snapshot Wire Shape

```json
{
  "snapshotId": "snapshot-epoch-0",
  "sessionId": "claude-session-1",
  "turnId": "turn-1",
  "compactionEpoch": 0,
  "segments": [
    {
      "segmentId": "segment-system",
      "kind": "system_prompt",
      "sourceRef": "runtime://system-prompt",
      "loadedAt": "startup",
      "reinjectionPolicy": "always",
      "guidanceRole": "neutral"
    }
  ],
  "createdAt": "2026-04-16T00:00:00Z",
  "metadata": {}
}
```

Contract rules:
- `segments` must contain at least one segment.
- A helper-created compaction snapshot must increment `compactionEpoch` by one.
- The original snapshot must remain unchanged after compaction.

## Compaction Helper Contract

`compact_claude_context_snapshot(...)` accepts:
- `snapshot`: existing `ClaudeContextSnapshot`.
- `snapshot_id`: new snapshot identifier.
- `work_item_id`: compaction work item identifier.
- `created_at`: timestamp for new snapshot, work item, and events.
- optional `turn_id` and `metadata`.

It returns `ClaudeContextCompactionResult` containing:
- `snapshot`: new post-compaction snapshot.
- `work_item`: `ClaudeManagedWorkItem` with `kind = "compaction"`.
- `events`: bounded `ClaudeContextEvent` records including `work.compaction.started` and `work.compaction.completed`.

Retention rules:
- Retain segments with `always`, `startup_refresh`, `budgeted`, and `configurable`.
- Omit segments with `on_demand` and `never`.
- Rewrite retained segments to `loadedAt = "post_compaction"`.

## Event Contract

Allowed event names:
- `work.context.loaded`
- `work.compaction.started`
- `work.compaction.completed`

All event metadata must pass compact Temporal mapping validation.
