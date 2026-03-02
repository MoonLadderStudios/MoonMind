# Quickstart: Manifest Queue Alignment and Hardening

## 1. Run Unit Test Suite

```bash
./tools/test_unit.sh
```

This repository wrapper is the required unit-test entrypoint.

## 2. Verify Manifest Registry Run Submission (Valid Action)

```bash
http POST :8000/api/manifests/demo/runs action=run options:='{"dryRun": true}'
```

Expected: HTTP `201` with `jobId`, `queue.type="manifest"`, and `queue.manifestHash`.

## 3. Verify Action Normalization

```bash
http POST :8000/api/manifests/demo/runs action=' PLAN '
```

Expected: HTTP `201` and service behavior equivalent to `action="plan"`.

## 4. Verify Fail-Fast Rejection for Unsupported Action

```bash
http POST :8000/api/manifests/demo/runs action=evaluate
```

Expected: HTTP `422` validation error; request is rejected before queue submission.

## 5. Verify Null/Type Guardrails

```bash
http POST :8000/api/manifests/demo/runs action:=null
http POST :8000/api/manifests/demo/runs action:=123
```

Expected: HTTP `422` validation errors indicating action must be a supported string value.

## 6. Verify Existing Manifest Queue Safety Behavior

1. Submit a valid manifest queue job (inline or registry path).
2. Fetch queue job detail (`/api/queue/jobs/{jobId}`).
3. Confirm payload metadata includes hash/capabilities and excludes raw inline manifest content/secrets.
