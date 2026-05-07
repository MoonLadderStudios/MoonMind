# Contract: Proposal Decision Ingestion

## Scope

This contract covers trusted ingestion of GitHub and Jira review decisions for delivered task proposals. External issue text is a review artifact only; MoonMind executes stored proposal snapshots.

## Provider Decision Input

Normalized provider decision input:

```json
{
  "provider": "github",
  "externalKey": "123",
  "providerEventId": "evt-abc",
  "actor": "reviewer-login",
  "action": "promote",
  "body": "/moonmind promote --runtime codex",
  "note": "ready",
  "observedAt": "2026-05-07T00:00:00Z",
  "authenticity": {
    "verified": true,
    "method": "signature"
  }
}
```

Rules:
- Provider authenticity must be verified before the event is parsed or applied.
- `providerEventId` is required for side-effecting decisions.
- `actor` must map to an authorized reviewer for the proposal destination.
- `action` may come from a trusted provider state transition, field change, label, or bounded command. Free-form issue body text must not become executable payload.

## Supported Decisions

| Decision | Effect | Run Created |
| --- | --- | --- |
| `promote` | Validate stored proposal snapshot plus bounded controls and create one MoonMind.Run | Yes |
| `dismiss` | Record rejection/dismissal note and final external state | No |
| `defer` | Record deferred state, note, and optional target | No |
| `reprioritize` | Update proposal review priority | No |
| `request_revision` | Record revision request note/state | No |

Unsupported decisions are rejected with sanitized reason metadata.

## Promotion Output

Successful provider promotion returns or records:

```json
{
  "accepted": true,
  "decision": "promote",
  "providerEventId": "evt-abc",
  "actor": "reviewer-login",
  "proposalId": "uuid",
  "promotedExecutionId": "workflow-id",
  "resultingExternalState": "promoted",
  "warnings": []
}
```

Rules:
- `promotedExecutionId` is created through the canonical run creation path.
- The run request uses the stored proposal snapshot plus validated bounded controls only.
- `task.authoredPresets`, explicit skill selectors, and `steps[].source` provenance must be preserved from the stored proposal payload.
- Duplicate provider events must return the prior decision outcome and create no additional run.

## Non-Executing Output

Accepted non-executing decisions record:

```json
{
  "accepted": true,
  "decision": "defer",
  "providerEventId": "evt-def",
  "actor": "reviewer-login",
  "proposalId": "uuid",
  "note": "after release freeze",
  "deferUntil": "2026-05-14",
  "resultingExternalState": "deferred"
}
```

Rules:
- Non-executing decisions never create a run.
- Audit metadata must include actor, provider event identity, note/reason when supplied, timestamp, requested state, and external issue state.
- Request revision must remain distinguishable from dismissal and deferral.

## Rejected Output

Rejected provider events record or return sanitized failure details:

```json
{
  "accepted": false,
  "decision": null,
  "providerEventId": "evt-bad",
  "actor": "unknown",
  "reason": "actor_not_authorized",
  "resultingExternalState": "ignored"
}
```

Rules:
- Rejected events must not create a run.
- Secrets, tokens, signatures, cookies, private keys, and authorization headers must be redacted from persisted and returned metadata.
- Provider authenticity failures must stop before action parsing when possible.

## Recovery Surface

Operator recovery must support inspecting and repairing proposal delivery state:

- Inspect proposal delivery and decision history.
- Redeliver or sync external issue state.
- Promote through the same validation path when explicitly requested by an authorized operator.
- Return promoted run identity or non-executing decision state.

Recovery actions must not bypass stored-snapshot validation, actor/policy checks, or idempotency.
