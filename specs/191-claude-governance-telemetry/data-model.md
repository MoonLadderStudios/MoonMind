# Data Model: Claude Governance Telemetry

## Claude Event Subscription

Represents a bounded request for a Claude evidence stream.

Fields:
- `subscriptionId`: stable nonblank subscription identifier.
- `subscriptionType`: session, group, or org_policy.
- `scopeId`: stable nonblank session, group, or org policy scope.
- `eventFamilies`: closed set of event families to include.
- `createdAt`: subscription creation timestamp.
- `metadata`: compact diagnostics.

Validation:
- Scope identifiers are required for every subscription.
- Event families reject unsupported values.
- Metadata remains compact and Temporal-safe.

## Claude Event Envelope

Represents one append-only normalized event.

Fields:
- `eventId`: stable nonblank event identifier.
- `eventName`: normalized event name from an allowed family.
- `eventFamily`: session, surface, policy, turn, work, decision, or child_work.
- `sessionId`, `sessionGroupId`, `policyEnvelopeId`, `turnId`, `workItemId`, `childContextId`, `surfaceId`: bounded identity references when relevant.
- `occurredAt`: event timestamp.
- `metadata`: compact metadata only.

Validation:
- Event name must belong to the declared family.
- Event metadata must not embed large payloads.
- Timestamps are timezone-normalized.

## Claude Storage Evidence

Represents payload-light storage posture for one session.

Fields:
- `evidenceId`: stable nonblank evidence identifier.
- `sessionId`: session reference.
- `centralStore`: session_registry, event_log, policy_store, context_index, checkpoint_index, artifact_index, or usage_store.
- `storedKinds`: metadata, event_envelope, policy_version, usage_counter, artifact_pointer, retention_metadata, telemetry_summary, governance_summary.
- `artifactRefs`: bounded artifact references.
- `runtimeLocalPayloadKinds`: transcript, full_file_read, checkpoint_payload, or local_cache.
- `payloadLight`: whether central-plane evidence stays payload-light.
- `createdAt`: evidence timestamp.
- `metadata`: compact diagnostics.

Validation:
- Default `payloadLight = true` rejects embedded source code, transcripts, full file reads, checkpoint payloads, and local caches.
- Artifact refs must be bounded nonblank references.

## Claude Retention Evidence

Represents policy-controlled retention classes.

Fields:
- `retentionId`: stable nonblank retention evidence identifier.
- `sessionId`: session reference.
- `classes`: one record for each required retention class.
- `policyRef`: bounded reference to the policy source.
- `createdAt`: evidence timestamp.

Required classes:
- hot_session_metadata
- hot_event_log
- usage_rollups
- audit_event_metadata
- checkpoint_payloads

Validation:
- Every required class must be present exactly once.
- Each class must be policy-controlled and must have a nonblank retention value.

## Claude Telemetry Evidence

Represents normalized Claude OpenTelemetry-derived signals.

Fields:
- `telemetryId`: stable nonblank telemetry evidence identifier.
- `sessionId`: session reference.
- `metrics`: normalized metric records.
- `eventEnvelopes`: normalized event/log envelopes.
- `traceSpans`: optional normalized trace span records.
- `createdAt`: evidence timestamp.

Validation:
- Metric names and trace span names come from the supported managed-session sets.
- Event/log records use the same event envelope validation as session evidence.

## Claude Usage Rollup

Represents usage evidence across audit dimensions.

Fields:
- `usageRollupId`: stable nonblank rollup identifier.
- `sessionId`: session reference.
- `sessionGroupId`: optional group reference.
- `userId`: user reference.
- `workspaceId`: workspace reference.
- `runtimeFamily`: Claude runtime family.
- `providerMode`: provider mode.
- `tokenDirection`: input, output, or total.
- `tokenCount`: non-negative count.
- `childContextId`: optional child context reference.
- `teamMemberSessionId`: optional team member session reference.
- `includedInParentRollup`: whether child/team usage is already included in parent totals.
- `createdAt`: evidence timestamp.

Validation:
- Child and team dimensions cannot both be set on the same rollup.
- Child rollups marked as included in the parent must not be marked as independent parent totals.

## Claude Governance Evidence

Represents an auditor-facing governance export record.

Fields:
- `governanceId`: stable nonblank governance evidence identifier.
- `sessionId`: session reference.
- `policyTrustLevel`: endpoint_enforced, server_managed_best_effort, or unmanaged.
- `providerMode`: provider mode.
- `executionSecurityMode`: local_execution, remote_control_projection, or cloud_execution.
- `controlLayers`: managed settings source resolution, permission rules, permission mode, protected paths, sandboxing, hooks, classifier auto mode, interactive dialogs, runtime isolation.
- `protectedPathPolicy`: dedicated protected-path behavior summary.
- `hookAudits`: hook audit records.
- `storageEvidenceRefs`, `retentionEvidenceRefs`, `telemetryEvidenceRefs`, `usageRollupRefs`: bounded evidence references.
- `createdAt`: evidence timestamp.
- `metadata`: compact diagnostics.

Validation:
- Protected-path policy is explicit when protected paths are part of the control layers.
- Hook audits use the existing hook audit validation.
- Evidence refs are bounded references, not embedded payloads.

## Claude Governance Fixture Flow

Deterministic provider-free fixture that models one synthetic session with surface activity, policy event, work event, decision event, child-work event, storage evidence, retention evidence, telemetry evidence, usage rollups, governance evidence, compliance export, and dashboard summary.

Validation:
- The flow includes at least one event envelope for each supported family.
- Central storage evidence remains payload-light.
- Compliance and dashboard summaries reference evidence by bounded identifiers.
