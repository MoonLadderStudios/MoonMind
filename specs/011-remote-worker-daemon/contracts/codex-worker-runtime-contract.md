# Codex Worker Runtime Contract (015-Aligned)

## Queue API Calls Used by Worker

### Claim Job

- **Method**: `POST /api/queue/jobs/claim`
- **Request**:
  - `workerId` (string)
  - `leaseSeconds` (int)
  - `allowedTypes` (array, optional; worker default includes `codex_exec`, `codex_skill`)
  - `workerCapabilities` (array, optional)
- **Expected Response**:
  - `{"job": null}` when no work
  - `{"job": <JobModel>}` when claim succeeds

### Heartbeat

- **Method**: `POST /api/queue/jobs/{jobId}/heartbeat`
- **Request**:
  - `workerId` (string)
  - `leaseSeconds` (int)

### Complete Job

- **Method**: `POST /api/queue/jobs/{jobId}/complete`
- **Request**:
  - `workerId` (string)
  - `resultSummary` (string, optional)

### Fail Job

- **Method**: `POST /api/queue/jobs/{jobId}/fail`
- **Request**:
  - `workerId` (string)
  - `errorMessage` (string)
  - `retryable` (bool)

### Append Event

- **Method**: `POST /api/queue/jobs/{jobId}/events`
- **Request**:
  - `workerId` (string)
  - `level` (`info|warn|error`)
  - `message` (string)
  - `payload` (object, optional)

### Upload Artifact

- **Method**: `POST /api/queue/jobs/{jobId}/artifacts/upload`
- **Multipart Fields**:
  - `file` (binary)
  - `name` (string)
  - `workerId` (string)
  - `contentType` (string, optional)
  - `digest` (string, optional)

## Worker Preflight Contract

Startup must fail fast unless all checks pass:

1. `verify_cli_is_executable("codex")`
2. `verify_cli_is_executable("speckit")`
3. `speckit --version`
4. `codex login status`
5. If `DEFAULT_EMBEDDING_PROVIDER=google`: require `GOOGLE_API_KEY` or `GEMINI_API_KEY`

## Worker Handler Input Contract

### `codex_exec`

Payload keys:

- `repository` (required)
- `instruction` (required)
- `ref` (optional)
- `workdirMode` (optional, default `fresh_clone`)
- `codex.model` (optional; task-level model override)
- `codex.effort` (optional; task-level effort override)
- `publish.mode` (optional, default `none`)
- `publish.baseBranch` (optional)

### `codex_skill`

Payload keys:

- `skillId` (optional, defaults to worker `default_skill`)
- `codex.model` (optional; task-level model override)
- `codex.effort` (optional; task-level effort override)
- `inputs` (optional object)
  - `repo` or `repository` (required logically for execution)
  - `instruction` (optional)
  - `ref`, `workdirMode`, `publishMode`, `publishBaseBranch` (optional)

Codex override precedence:

1. `payload.codex.model` / `payload.codex.effort`
2. Worker defaults (`MOONMIND_CODEX_MODEL`/`MOONMIND_CODEX_EFFORT`, falling back to `CODEX_MODEL`/`CODEX_MODEL_REASONING_EFFORT`)
3. Codex CLI defaults

Execution behavior:

- `skillId=speckit` -> skills path (`executionPath=skill`)
- allowlisted non-Speckit skill -> compatibility fallback (`executionPath=direct_fallback`)
- non-allowlisted skill -> immediate job failure

## Event Payload Execution Metadata Contract

Worker event payloads for claim/start/complete/fail include:

- `selectedSkill` (string)
- `executionPath` (`skill|direct_fallback|direct_only`)
- `usedSkills` (bool)
- `usedFallback` (bool)
- `shadowModeRequested` (bool)

## Local Artifact Contract

For processed execution jobs, worker may write and upload:

- `logs/codex_exec.log`
- `patches/changes.patch`
