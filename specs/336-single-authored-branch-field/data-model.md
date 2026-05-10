# Data Model: Single Authored Branch Field

## Authored Task Submission

Represents the task input created, saved, submitted, snapshotted, or prepared under the current authored contract.

Fields:
- `git.branch`: Optional string unless the selected publish mode requires an explicit branch. It is the only active authored branch value.
- `publish.mode`: One of `none`, `branch`, or `pr`.
- `publishMode`: Top-level transport or compatibility projection of the selected publish mode where existing task submission routes require it.
- `runtime`: Existing runtime selection metadata.
- `steps`: Existing task step payloads.

Validation rules:
- New authored submissions must not contain active `git.targetBranch`, top-level `targetBranch`, `git.startingBranch`, or top-level `startingBranch`.
- If publish mode requires branch intent and `git.branch` is blank, the submission must fail validation rather than derive a branch from legacy fields.
- `publish.mode` must remain present whenever publish behavior is selected.

## Legacy Task Snapshot

Represents an older persisted task input or execution payload being reconstructed for display, edit, rerun, or audit.

Fields:
- `startingBranch`: Historical branch value that may be normalized to `git.branch` when no conflicting branch intent exists.
- `targetBranch`: Historical branch value retained only for audit/diagnostic context.
- `publishMode` or `publish.mode`: Historical publish mode used to interpret warning severity.

Validation and reconstruction rules:
- `startingBranch` may become the reconstructed authored branch when it is the only branch intent, or when `targetBranch` is absent or identical.
- `targetBranch` must not become the reconstructed authored branch when `startingBranch` is absent.
- Two-branch branch-publish snapshots that cannot be represented by one authored branch must produce a reconstruction warning.
- Reconstructed submissions must submit only `git.branch`, not legacy branch fields.

## Branch Intent Metadata

Represents historical branch values retained for audit/debug display.

Fields:
- `legacyStartingBranch`: Optional historical value.
- `legacyTargetBranch`: Optional historical value.
- `source`: Snapshot, execution projection, artifact, or runtime diagnostic source.

Validation rules:
- Metadata is not an authored input.
- Metadata must not influence active branch selection for edit, rerun, resubmission, or runtime preparation.

## Reconstruction Warning

Represents operator-visible evidence that a legacy snapshot could not be reconstructed exactly.

Fields:
- `message`: Human-readable warning.
- `reason`: Stable reason such as `target_only_legacy_branch` or `two_branch_branch_publish`.
- `legacyStartingBranch`: Optional historical value.
- `legacyTargetBranch`: Optional historical value.

State transitions:
- `none`: Snapshot is current or safely normalized.
- `warning`: Snapshot can be reviewed but had historical branch intent that cannot round-trip exactly.
- `blocked`: Snapshot lacks required authored branch after reconstruction and publish mode requires one.

## Runtime Branch Resolution

Represents runtime-owned branch decisions after task submission.

Fields:
- `authoredBranch`: Derived only from `git.branch`.
- `workingBranch`: Runtime-owned branch used for local work.
- `headBranch`: Runtime-owned PR head branch where applicable.

Validation rules:
- Runtime branch resolution must not read authored `git.targetBranch` as an input.
- Runtime may emit generated head/working branch metadata for diagnostics and publishing, but that metadata is not an authored task field.
