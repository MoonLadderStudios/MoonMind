# Contract: Claude Governance Telemetry

## Schema Exports

The following symbols are exported from `moonmind.schemas`:

- `ClaudeEventFamily`
- `ClaudeEventSubscription`
- `ClaudeEventSubscriptionType`
- `ClaudeEventEnvelope`
- `ClaudeCentralStoreKind`
- `ClaudeStoredEvidenceKind`
- `ClaudeRuntimeLocalPayloadKind`
- `ClaudeStorageEvidence`
- `ClaudeRetentionClassName`
- `ClaudeRetentionClass`
- `ClaudeRetentionEvidence`
- `ClaudeTelemetryMetricName`
- `ClaudeTelemetrySpanName`
- `ClaudeTelemetryMetric`
- `ClaudeTelemetrySpan`
- `ClaudeTelemetryEvidence`
- `ClaudeUsageTokenDirection`
- `ClaudeUsageRollup`
- `ClaudeGovernanceControlLayer`
- `ClaudeGovernanceEvidence`
- `ClaudeComplianceExportView`
- `ClaudeProviderDashboardSummary`
- `ClaudeGovernanceTelemetryFixtureFlow`
- `build_claude_governance_telemetry_fixture_flow`

## Event Subscription

### `ClaudeEventSubscription`

Accepts:
- `subscriptionId`
- `subscriptionType`: `session`, `group`, or `org_policy`
- `scopeId`
- `eventFamilies`
- `createdAt`
- optional compact `metadata`

Validation:
- Blank identifiers fail.
- Unsupported families fail.
- Empty `eventFamilies` fails.

## Event Envelope

### `ClaudeEventEnvelope`

Accepts:
- `eventId`
- `eventFamily`
- `eventName`
- relevant identity refs
- `occurredAt`
- compact `metadata`

Validation:
- Event name must belong to the declared family.
- Session family events require `sessionId`.
- Group child-work events may carry `sessionGroupId`.
- Metadata must remain compact and payload-light.

## Storage Evidence

### `ClaudeStorageEvidence`

Accepts central store, stored evidence kinds, runtime-local payload class names, bounded artifact refs, and `payloadLight`.

Validation:
- `payloadLight = true` rejects embedded payload fields in metadata, including source code, transcript, full file read, checkpoint payload, and local cache keys.
- `artifactRefs` are refs only.
- Store and evidence kind values are closed.

## Retention Evidence

### `ClaudeRetentionEvidence`

Accepts one `ClaudeRetentionClass` for each required retention class and a policy ref.

Validation:
- Required classes must be complete and unique.
- Every class must be policy-controlled.
- Blank retention values fail.

## Telemetry Evidence

### `ClaudeTelemetryEvidence`

Accepts normalized metrics, event envelopes, optional trace spans, and a session ref.

Validation:
- Metric names and trace span names are closed sets.
- Event envelopes use `ClaudeEventEnvelope` validation.

## Usage Rollup

### `ClaudeUsageRollup`

Accepts usage dimensions for session, group, user, workspace, runtime family, provider mode, token direction, and optional child or team identity.

Validation:
- Token counts are non-negative.
- Child and team dimensions are mutually exclusive.
- Independent parent totals cannot also be marked as child-included rollups.

## Governance Evidence

### `ClaudeGovernanceEvidence`

Accepts governance fields for policy trust level, provider mode, execution security mode, control layers, protected-path policy, hook audit records, and bounded refs to storage, retention, telemetry, and usage evidence.

Validation:
- Protected-path control layer requires `protectedPathPolicy`.
- Hook audits use `ClaudeHookAudit`.
- Evidence refs are nonblank refs.

## Fixture Flow

### `build_claude_governance_telemetry_fixture_flow(...) -> ClaudeGovernanceTelemetryFixtureFlow`

Builds a deterministic provider-free flow containing:
- event subscription
- event envelopes across all supported families
- payload-light storage evidence
- policy-controlled retention evidence
- telemetry evidence
- usage rollups
- governance evidence
- compliance export view
- provider dashboard summary

The fixture is for unit and integration-style schema boundary tests. It does not call live Claude providers or external telemetry backends.
