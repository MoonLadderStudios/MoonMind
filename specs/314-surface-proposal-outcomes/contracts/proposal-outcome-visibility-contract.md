# Contract: Proposal Outcome Visibility

## Finish Summary Contract

Run finish summaries and exported run summaries expose:

```json
{
  "proposals": {
    "requested": true,
    "generatedCount": 2,
    "submittedCount": 2,
    "deliveredCount": 1,
    "validationErrors": [
      {"code": "proposal_missing_task", "message": "proposal skipped: task payload missing"}
    ],
    "deliveryFailures": [
      {"provider": "jira", "code": "delivery_failed", "message": "delivery failed: [REDACTED]"}
    ],
    "externalLinks": [
      {"provider": "jira", "externalKey": "MM-901", "externalUrl": "https://jira.example/browse/MM-901"}
    ],
    "dedupUpdates": [
      {"provider": "github", "externalKey": "42", "created": false, "duplicateSource": "existing-open-issue"}
    ]
  }
}
```

Rules:
- Summary entries are compact.
- Validation and delivery errors are redacted.
- Full task snapshots and external issue body text are excluded.

## Execution Detail / Mission Control Contract

Execution detail and Mission Control surfaces expose proposal outcomes for the source run:

```json
{
  "proposalOutcomes": [
    {
      "proposalId": "uuid",
      "provider": "jira",
      "externalKey": "MM-901",
      "externalUrl": "https://jira.example/browse/MM-901",
      "deliveryStatus": "delivered",
      "lastSyncedAt": "2026-05-07T12:45:00Z",
      "created": true,
      "duplicateSource": null,
      "taskSummary": {
        "runtime": "codex",
        "repository": "MoonMind/MoonMind",
        "publishMode": "pr",
        "priority": 0,
        "maxAttempts": 3,
        "skillContext": ["moonspec-implement"],
        "presetProvenance": "preserved-binding"
      },
      "promotionResult": {
        "promotedExecutionId": "wf-promoted-1",
        "promotedExecutionUrl": "/tasks/temporal/wf-promoted-1"
      }
    }
  ]
}
```

Rules:
- Missing source values are omitted or shown as unavailable.
- The compact task summary is review information only.
- Promotion links appear only when promotion has occurred.

## State Compatibility Contract

While proposals are being generated, submitted, or delivered:

```json
{
  "mm_state": "proposals",
  "dashboardStatus": "running"
}
```

Rules:
- API consumers can still identify the specific `proposals` state.
- Dashboard compatibility continues to group `proposals` with running work.

## Review Path Contract

Normal proposal review uses:
- GitHub Issues
- Jira issues

MoonMind UI may show:
- source run finish summary
- execution detail delivery links
- compact delivery status cards
- admin/recovery views

MoonMind UI must not require a standalone proposal queue page as the normal Promote/Dismiss review path.
