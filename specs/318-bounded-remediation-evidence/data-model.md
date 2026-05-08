# Data Model: Bounded Remediation Evidence Context

## Remediation Context Artifact

Represents the stable evidence entrypoint for one remediation task.

Fields:
- `schemaVersion`: version string, currently `v1`.
- `remediationWorkflowId`: logical workflow ID of the remediation run.
- `generatedAt`: ISO timestamp for context generation.
- `target`: `TargetExecutionSnapshot`.
- `selectedSteps`: ordered list of `SelectedStepEvidence`.
- `evidence`: `EvidenceBundle`.
- `liveFollow`: `LiveFollowState`.
- `policies`: `RemediationPolicySnapshot`.
- `boundedness`: limits and booleans proving raw logs/artifact bodies are not embedded.

Validation rules:
- Must be valid JSON and linked as artifact type `remediation.context`.
- Must be linked to the remediation execution with label `reports/remediation_context.json`.
- Must not include presigned URLs, storage backend keys, absolute local paths, or secret values.
- Must include refs and compact summaries only; full evidence bodies remain behind server-mediated reads.

## TargetExecutionSnapshot

Compact target identity pinned for remediation diagnosis.

Fields:
- `workflowId`: target logical execution ID.
- `runId`: pinned target run ID for the context.
- `title`: compact title when available.
- `summary`: compact summary when available.
- `state`: current lifecycle state at context generation.
- `closeStatus`: terminal close status when available.

Validation rules:
- `workflowId` and `runId` are required.
- `runId` is the pinned target run, not a mutable latest-run alias.

## SelectedStepEvidence

Optional bounded selector for evidence the remediation task should prioritize.

Fields:
- `logicalStepId`: optional logical step identifier.
- `attempt`: optional positive attempt number.
- `taskRunId`: optional task-run identifier for logs/diagnostics/live follow.
- `status`: optional compact step status when available.
- `summary`: optional compact step summary when available.
- `artifactRefs`: optional list of step-scoped `EvidenceRef` values.

Validation rules:
- At least one identifying field must be present.
- Total selected task-run IDs must remain capped by the context builder.

## EvidenceBundle

Collection of server-mediated refs and availability records.

Fields:
- `targetArtifactRefs`: refs to target-level artifacts such as input, plan, manifest, run summaries, provider snapshots, and continuity artifacts.
- `taskRuns`: list of `TaskRunEvidence` entries.
- `availability`: list of `EvidenceAvailabilityRecord` entries.
- `diagnosisHints`: optional compact hints derived from known state, never raw logs.

Validation rules:
- All refs are identifiers, not access grants.
- Missing or unavailable evidence is recorded explicitly rather than causing context generation to deadlock when other evidence remains available.

## TaskRunEvidence

Evidence refs for one target task run.

Fields:
- `taskRunId`: task-run ID.
- `observabilitySummaryRef`: optional artifact ref.
- `stdoutRef`: optional artifact ref.
- `stderrRef`: optional artifact ref.
- `mergedLogsRef`: optional artifact ref.
- `diagnosticsRef`: optional artifact ref.
- `providerSnapshotRef`: optional artifact ref.
- `continuityRefs`: optional continuity or control-boundary refs.

Validation rules:
- `taskRunId` is required.
- Each ref must be readable only through server-mediated artifact/log surfaces.

## EvidenceAvailabilityRecord

Records evidence class status.

Fields:
- `class`: evidence class such as `stdout`, `stderr`, `merged_logs`, `diagnostics`, `provider_snapshot`, `continuity`, or `live_follow`.
- `status`: `available`, `missing`, `partial`, `denied`, `unavailable`, or `fallback_used`.
- `reason`: compact operator-safe reason when unavailable or degraded.
- `fallback`: optional fallback evidence class.

Validation rules:
- Missing, partial, denied, and unavailable evidence must not include sensitive backend details.
- Historical merged-log-only targets set degraded evidence when richer evidence is absent.

## LiveFollowState

Best-effort live observation state for the target.

Fields:
- `status`: `active`, `unavailable`, `unsupported`, or `policy_denied`.
- `mode`: remediation mode, for example `snapshot`, `follow`, or `snapshot_then_follow`.
- `supported`: boolean compatibility field for the typed evidence tool.
- `taskRunId`: live-follow target task run when active or selected.
- `resumeCursor`: compact cursor such as last seen sequence when available.
- `reason`: compact reason when not active.
- `fallbacks`: durable fallback evidence classes.

Validation rules:
- Live follow may be active only when target run is active, target task run supports live follow, and policy permits it.
- Live follow never replaces durable artifact/log evidence.
- Cursor state must be compact and safe to carry across retries or continue-as-new boundaries.

## RemediationPolicySnapshot

Compact policy state captured for evidence access and action decisions.

Fields:
- `authorityMode`: remediation authority mode.
- `actionPolicyRef`: action policy identifier when present.
- `evidencePolicy`: bounded evidence policy such as log tail limit and included classes.
- `approvalPolicy`: compact approval policy.
- `lockPolicy`: compact target mutation lock policy.

Validation rules:
- Secret-like keys and values are removed.
- Unsupported authority/action policy values fail fast elsewhere; context does not invent fallbacks.

## State Transitions

Context generation:
1. Remediation link exists with target workflow and pinned run.
2. Builder resolves target record, selected steps, evidence refs, policy snapshots, and live-follow state.
3. Builder writes restricted `remediation.context` artifact and stores `context_artifact_ref` on the remediation link.
4. Remediation task uses typed evidence tools to read context, artifacts, logs, or live-follow batches.

Live follow:
1. `unsupported`, `unavailable`, or `policy_denied` states use durable fallback evidence.
2. `active` state may advance `resumeCursor` as events are consumed.
3. Disconnects or retries resume from durable cursor when available.

Action preparation:
1. Remediation task requests an action.
2. Typed evidence service rereads current target health.
3. Action execution proceeds only through a separate authorized action path and publishes bounded lifecycle artifacts.
