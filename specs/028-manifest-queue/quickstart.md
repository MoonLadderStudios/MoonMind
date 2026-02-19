# Quickstart: Manifest Queue Plumbing (Phase 0)

## 1. Run Unit Tests

```bash
./tools/test_unit.sh
```
The wrapper script ensures Python 3.11 + pytest run consistently across local + CI. Add or update tests under `tests/unit`/`api_service/tests` so they are exercised by this command (e.g., manifest contract + manifests API suites).

## 2. Submit an Inline Manifest Job

```bash
http POST :8000/api/queue/jobs \
  type=manifest \
  manifest:='{
    "name": "sample",
    "action": "run",
    "source": {"kind": "inline", "content": "version: \"v0\"\nmetadata:\n  name: sample\n..."},
    "options": {"dryRun": false}
  }'
```
Expected response shows the new job id plus derived capabilities.

## 3. Create Registry Entry + Run

```bash
http PUT :8000/api/manifests/sample content="$(cat sample.yaml)" version=v0
http POST :8000/api/manifests/sample/runs action=run options:='{"forceFull": false}'
```
Verify the response includes the queue job id and `manifestHash`. Follow with `GET /api/manifests/sample` to ensure `lastRunJobId` updated.

## 4. Observe Queue Detail

Use the Tasks Dashboard (local front-end) or `http GET :8000/api/queue/jobs/{jobId}` to confirm the job is categorized under `manifest` and exposes derived capabilities + hash metadata without raw secrets.

## 5. Error Handling Smoke Tests

- Submit a manifest where `payload.manifest.name` differs from `metadata.name` → expect 400 with validation message.
- Submit `POST /api/manifests/unknown/runs` → expect 404.

These steps confirm all runtime pieces (allowlist, contract, registry endpoints) are wired and testable.
