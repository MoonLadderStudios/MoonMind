# Quickstart: Manifest Queue Phase 0 Alignment

**Feature**: Manifest Queue Phase 0 Alignment

## Goal

Verify manifest validation failures now return actionable errors on queue and registry submission paths.

## Prerequisites

- API service running locally.
- Auth token exported as `MOONMIND_API_TOKEN`.

## 1. Validate Queue Error Behavior for Manifest Jobs

Submit an invalid manifest queue payload:

```bash
curl -sS -X POST "http://localhost:5000/api/queue/jobs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "manifest",
    "payload": {
      "manifest": {
        "name": "demo",
        "source": {"kind": "inline", "content": "version: v0\nmetadata:\n  name: other\n"}
      }
    }
  }' | jq .
```

Expected:

- HTTP `422`
- `detail.code == "invalid_manifest_job"`
- `detail.message` contains actionable contract text (for example name mismatch details)

## 1b. Validate Non-Manifest Queue Regression Behavior

Submit a non-manifest queue payload that fails generic validation:

```bash
curl -sS -X POST "http://localhost:5000/api/queue/jobs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "task",
    "payload": {
      "repository": "MoonMind/repo"
    }
  }' | jq .
```

Expected:

- HTTP `422`
- `detail.code == "invalid_queue_payload"`
- `detail.message == "Queue request payload is invalid."`

## 2. Validate Registry Upsert Error Behavior

Submit invalid manifest YAML to registry:

```bash
curl -sS -X PUT "http://localhost:5000/api/manifests/demo" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"version: v0\nmetadata:\n  name: other\n"}' | jq .
```

Expected:

- HTTP `422`
- `detail.code == "invalid_manifest"`
- `detail.message` contains contract-derived validation text

## 3. Run Unit Suite

```bash
./tools/test_unit.sh
```

Expected: all unit tests pass.

## 4. Validation Evidence

- Date (UTC): `2026-03-02`
- Command: `./tools/test_unit.sh`
- Result: `PASS` (`898 passed, 8 subtests passed`)
