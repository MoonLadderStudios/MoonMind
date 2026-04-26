# Data Model: Explicit Failure and Rollback Controls

## Deployment Failure Result

- `status`: `FAILED` or `PARTIALLY_VERIFIED`.
- `failureClass`: one of `invalid_input`, `authorization_failure`, `policy_violation`, `deployment_lock_unavailable`, `compose_config_validation_failure`, `image_pull_failure`, `service_recreation_failure`, `verification_failure`, or `evidence_failure`.
- `failureReason`: non-empty actionable operator-facing reason.
- `retryable`: false by default for privileged deployment-control failures.
- `artifactRefs`: available before-state, command-log, verification, and after-state refs when lifecycle phases reached them.
- `audit`: run/workflow/task IDs where available, operator, role, reason, requested image, mode, timestamps, and final status.

Validation rules:
- `SUCCEEDED` is never valid when `failureClass` is present.
- Failure records must redact secrets before publication or UI display.
- Missing evidence must fail closed and record the missing phase when possible.

## Rollback Eligibility Decision

- `eligible`: boolean.
- `sourceActionId`: recent deployment action or run identifier that produced before-state evidence.
- `targetImage`: previous safe image reference when eligible.
- `reason`: short operator-facing explanation when ineligible.
- `evidenceRef`: before-state artifact ref or trusted projection source used to derive the target.

Validation rules:
- Eligibility is true only when before-state evidence identifies exactly one allowlisted repository/reference or digest target.
- Missing, ambiguous, malformed, non-allowlisted, or untrusted evidence makes eligibility false.
- The decision does not mutate deployment state.

## Rollback Request

- `stack`: allowlisted stack, currently `moonmind`.
- `image.repository`: allowlisted MoonMind image repository.
- `image.reference`: target previous image tag or digest from rollback eligibility.
- `mode`: normal deployment update mode, usually `changed_services` unless the operator explicitly chooses another policy-allowed mode.
- `reason`: required operator reason.
- `confirmation`: operator confirmation that the rollback target and restart implications are understood.
- `sourceActionId`: optional recent action used to derive rollback target.
- `operationKind`: `rollback`.

Validation rules:
- Rollback uses the same typed deployment update path as forward updates.
- Admin authorization, reason, confirmation, deployment lock, before/after artifacts, and verification remain required.
- Rollback cannot include shell commands, runner images, host paths, non-allowlisted stacks, or non-allowlisted repositories.

## Deployment Audit Action

- `id` or `runId`: stable action identifier.
- `kind`: `update`, `failure`, or `rollback`.
- `status`: final deployment status.
- `requestedImage`: target image.
- `resolvedDigest`: optional digest evidence.
- `operator`: visible operator identity when available.
- `reason`: update or rollback reason.
- `startedAt` / `completedAt`: lifecycle timestamps.
- `runDetailUrl`: link to the run detail page when available.
- `logsArtifactUrl`: redacted logs or command artifact link when available.
- `rawCommandLogUrl`: present only when operational-admin policy permits.
- `beforeSummary` / `afterSummary`: compact before/after evidence.
- `rollbackEligibility`: optional rollback eligibility decision for failed or previous actions.

State transitions:
- `update submitted` -> `failure result` may produce a recent `failure` action.
- `failure action` -> `rollback eligible` only when trusted before-state evidence is sufficient.
- `rollback eligible` -> `rollback submitted` only after explicit admin confirmation.
- `rollback submitted` -> normal deployment update lifecycle with its own artifacts and audit action.
