# Contract: Proposal Intent Normalization

## API Submission Contract

When a new task-shaped execution request includes proposal intent, the durable run input must contain:

```json
{
  "task": {
    "proposeTasks": true,
    "proposalPolicy": {
      "targets": ["project", "moonmind"],
      "maxItems": {
        "project": 2,
        "moonmind": 1
      },
      "minSeverityForMoonMind": "medium",
      "defaultRuntime": "gemini_cli"
    }
  }
}
```

New submissions must not write these proposal-intent fields as root-level run parameters:

```json
{
  "proposeTasks": true,
  "proposalPolicy": {}
}
```

## Temporal Workflow Gate Contract

The proposal stage starts only when both conditions are true:

1. Global workflow proposal generation is enabled.
2. Canonical `parameters.task.proposeTasks` is true.

Older root-only `parameters.proposeTasks` may be read only by compatibility logic for already-persisted workflow payloads. Compatibility reads must be covered by workflow-boundary tests and must not appear in new-write API output.

## Proposal Policy Contract

The proposal stage reads policy from `parameters.task.proposalPolicy`.

The proposal stage must ignore flattened legacy policy fields such as:

- `proposalMaxItems`
- `proposalTargets`
- `proposalDefaultRuntime`

## Managed Runtime Task Creation Contract

Managed runtimes that create MoonMind tasks must send proposal intent through the canonical nested task payload:

```json
{
  "repository": "owner/repo",
  "task": {
    "instructions": "Do the follow-up work.",
    "proposeTasks": true,
    "proposalPolicy": {
      "targets": ["project"]
    }
  }
}
```

Runtime-local metadata, turn metadata, container environment, and adapter-local fields are not durable proposal-intent contracts.

## Status Vocabulary Contract

Proposal-capable execution state must use `proposals` consistently in:

- Temporal workflow state
- execution API state payloads
- Mission Control status mapping
- run finish summary proposal metadata
- touched documentation references
