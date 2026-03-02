# Data Model: Manifest Queue Alignment and Hardening

## Manifest Run Request Model

`POST /api/manifests/{name}/runs` consumes `ManifestRunRequest`.

| Field | Type | Required | Rules |
|-------|------|----------|-------|
| `action` | string | no | Defaults to `run`; accepted values: `plan`, `run`; normalized to lowercase/trimmed form. |
| `options.dryRun` | boolean | no | Optional run override. |
| `options.forceFull` | boolean | no | Optional run override. |
| `options.maxDocs` | integer/null | no | Must be `>= 1` when provided. |

### Validation Semantics

1. Unsupported action values fail request validation before service invocation.
2. Missing action is normalized to `run`.
3. Whitespace and mixed-case valid actions are normalized (`" PLAN "` -> `"plan"`).
4. Non-string action payloads fail validation (`action must be a string and one of: plan, run`).
5. Explicit `null` action fails validation (`action must be one of: plan, run`).

## Manifest Run Queue Metadata (Response)

`ManifestRunResponse.queue` includes operator-facing queue metadata:

| Field | Type | Source |
|-------|------|--------|
| `type` | string | `AgentJob.type` (expected `manifest`) |
| `requiredCapabilities` | array[string] | normalized queue payload |
| `manifestHash` | string/null | normalized queue payload |

## Persisted Queue Payload (Unchanged by this feature)

Manifest queue payloads remain normalized by `moonmind/workflows/agent_queue/manifest_contract.py` and include:

- `manifest` (`name`, `action`, normalized `source`, optional `options`)
- `manifestHash`
- `manifestVersion`
- `requiredCapabilities`
- `effectiveRunConfig`
- optional `manifestSecretRefs`

This feature does not alter queue-payload normalization, hashing, capability derivation, or secret-reference extraction semantics.

## Registry Record Fields (Current Runtime)

`ManifestRecord` currently includes runtime-relevant metadata used by `/api/manifests` endpoints:

- `name`, `content`, `content_hash`, `version`
- `last_run_job_id`, `last_run_status`, `last_run_started_at`, `last_run_finished_at`
- `state_json`, `state_updated_at`
- `created_at`, `updated_at`, `last_indexed_at`
