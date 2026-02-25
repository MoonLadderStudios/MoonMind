# Data Model: Task Finish Summary System

## Entity: FinishOutcome

- **Description**: Compact terminal classification persisted for queue list friendliness and triage.
- **Fields**:
  - `code` (enum): one of `PUBLISHED_PR`, `PUBLISHED_BRANCH`, `NO_CHANGES`, `PUBLISH_DISABLED`, `FAILED`, `CANCELLED`.
  - `stage` (enum): one of `prepare`, `execute`, `publish`, `proposals`, `finalize`, `unknown`.
  - `reason` (string, <=256 chars persisted in list surface): short redacted explanation for terminal state.
- **Rules**:
  - `code`, `stage`, and `reason` must be persisted on terminal transitions when available.
  - `FAILED` and `CANCELLED` outcomes must not be overwritten by publish-mode heuristics.

## Entity: FinishSummary

- **Description**: Canonical terminal payload persisted in DB and emitted as `reports/run_summary.json`.
- **Fields**:
  - `schemaVersion` (string): currently `v1`.
  - `jobId` (UUID string).
  - `jobType` (string): queue job type (task scope for this feature).
  - `repository` (string).
  - `targetRuntime` (string): runtime adapter (for this feature, codex path).
  - `timestamps` (object): `startedAt`, `finishedAt`, `durationMs`.
  - `finishOutcome` (FinishOutcome).
  - `stages` (map of stage name -> StageResult).
  - `publish` (PublishOutcome).
  - `changes` (object): `hasChanges` (bool), `patchArtifact` (string path).
  - `proposals` (ProposalOutcome).
- **Rules**:
  - Payload must remain non-secret and redacted before persistence.
  - DB JSON and artifact JSON must share identical schema.
  - List endpoints should not include this object by default.

## Entity: StageResult

- **Description**: Per-stage execution telemetry used for detail diagnostics.
- **Fields**:
  - `status` (enum): `succeeded`, `failed`, `skipped`, `not_run`.
  - `durationMs` (integer, optional): non-negative elapsed time.
- **Rules**:
  - Stage keys include `prepare`, `execute`, `publish`, `proposals`, and `finalize`.
  - Completed stages should include `durationMs`; untouched stages remain `not_run`.

## Entity: PublishOutcome

- **Description**: Normalized publish result captured in finish summary.
- **Fields**:
  - `mode` (string): publish mode from payload (`pr`, `branch`, `none`, etc.).
  - `status` (enum): `published`, `skipped`, `failed`, `not_run`.
  - `reason` (string | null): short, redacted reason.
  - `workingBranch` (string | null).
  - `baseBranch` (string | null).
  - `prUrl` (string | null).
- **Rules**:
  - `mode=none` with successful prior stages maps to `PUBLISH_DISABLED` outcome.
  - `status=skipped` with no-change reason maps to `NO_CHANGES` outcome.

## Entity: ProposalOutcome

- **Description**: Proposal finisher output summary attached to finish summary.
- **Fields**:
  - `requested` (bool): whether proposal generation was requested.
  - `hookSkills` (list[string]).
  - `generatedCount` (integer >=0).
  - `submittedCount` (integer >=0).
  - `errors` (list[string]): short redacted error reasons.
- **Rules**:
  - `submittedCount` cannot exceed `generatedCount`.
  - Errors must avoid stack traces/secrets.

## Entity: QueueJobFinishPersistence

- **Description**: Additive queue storage extension on `agent_jobs` for finish metadata.
- **Fields**:
  - `finish_outcome_code` (nullable string(64)).
  - `finish_outcome_stage` (nullable string(32)).
  - `finish_outcome_reason` (nullable string(256)).
  - `finish_summary_json` (nullable JSON/JSONB object).
- **Rules**:
  - Fields are set on terminal transitions and cleared/reset on clone/requeue flows when applicable.
  - Read models must expose code/stage/reason to list/detail; full summary detail is optional in list and included in detail.

## Entity: ProposalsOriginFilter

- **Description**: API/UI query model for deep-linking proposals from queue job detail.
- **Fields**:
  - `originSource` (enum): includes `queue`.
  - `originId` (UUID, optional but required for exact run filtering use case).
- **Rules**:
  - When both fields are provided, proposals list returns only records tied to that queue run.
  - UI query-param parsing must initialize filter controls consistently on page load.
