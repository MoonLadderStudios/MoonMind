# Data Model: Settings Operations Deployment Update UI

## DeploymentStackState

- `stack`: allowlisted stack identifier.
- `projectName`: Compose project name.
- `configuredImage`: configured MoonMind image reference.
- `runningImages`: service image evidence; each item may include `imageId` or `digest`.
- `services`: service state and optional health.
- `lastUpdateRunId`: optional recent run identifier.
- Optional UI-only tolerated fields: `version`, `build`, `recentActions`.

## ImageTargets

- `stack`: stack identifier.
- `repositories`: allowlisted repositories with `allowedReferences`, `recentTags`, and `digestPinningRecommended`.

## DeploymentUpdateForm

- `stack`
- `repository`
- `reference`
- `mode`: `changed_services` by default, `force_recreate` only when available.
- `removeOrphans`, `wait`, `runSmokeCheck`, `pauseWork`, `pruneOldImages`
- `reason`: optional operator note.

## Validation Rules

- Target image controls expose repository/reference only; updater runner image is not a form field.
- Mutable references such as `latest` produce a visible warning and confirmation warning.
- Submit is blocked when no target repository/reference is available.
