# Quickstart: Temporal Task Draft Reconstruction

## Preconditions

- Run from repository root: `/work/agent_jobs/mm:e407d769-fdd6-4c71-907a-f2134715e8ed/repo`
- Frontend dependencies are installed. If `node_modules` is missing, use `./tools/test_unit.sh` once to prepare dependencies through the repo test runner.

## Targeted Validation

Run the shared submit page tests:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
```

Run existing detail-page task editing route tests:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx
```

Run frontend typecheck:

```bash
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
```

Run the full required unit suite:

```bash
./tools/test_unit.sh
```

## Manual Smoke Path

1. Enable the `temporalTaskEditing` feature flag in local runtime configuration.
2. Open a supported active `MoonMind.Run` detail page that exposes Edit.
3. Navigate to edit mode.
4. Confirm the shared submit page title is `Edit Task`.
5. Confirm runtime, provider profile, model, effort, repository, branches, publish mode, task instructions, primary skill, and template state are prefilled where available.
6. Confirm recurring schedule controls are not shown in edit mode.
7. Open a supported terminal `MoonMind.Run` detail page that exposes Rerun.
8. Navigate to rerun mode.
9. Confirm the shared submit page title and primary CTA are rerun-specific.
10. Confirm unsupported workflow types, missing capability flags, and missing/unreadable instruction artifacts produce explicit errors.

## Out-of-Scope Validation

- Do not expect `UpdateInputs` submission in this phase.
- Do not expect `RequestRerun` submission in this phase.
- Do not test legacy queue routes as a fallback path; their use is explicitly forbidden for this feature.
