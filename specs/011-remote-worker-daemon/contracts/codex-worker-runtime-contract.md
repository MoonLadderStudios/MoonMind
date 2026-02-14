# Codex Worker Runtime Contract (Milestone 3)

## Queue API Calls Used by Worker

### Claim Job

- **Method**: `POST /api/queue/jobs/claim`
- **Request**:
  - `workerId` (string)
  - `leaseSeconds` (int)
  - `allowedTypes` (array, optional)
- **Expected Response**:
  - `{"job": null}` when no work
  - `{"job": <JobModel>}` when claim succeeds

### Heartbeat

- **Method**: `POST /api/queue/jobs/{jobId}/heartbeat`
- **Request**:
  - `workerId` (string)
  - `leaseSeconds` (int)
- **Expected Response**:
  - Updated `JobModel`

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

### Upload Artifact

- **Method**: `POST /api/queue/jobs/{jobId}/artifacts/upload`
- **Multipart Fields**:
  - `file` (binary)
  - `name` (string)
  - `contentType` (string, optional)
  - `digest` (string, optional)

## Worker Handler Input Contract

### Supported Job Type: `codex_exec`

Payload keys expected by milestone implementation:

- `repository` (required)
- `instruction` (required)
- `ref` (optional, default `main`)
- `workdirMode` (optional, default `fresh_clone`)
- `publish.mode` (optional, default `none`)
- `publish.baseBranch` (optional)

## Local Artifact Contract

For each processed `codex_exec` job, worker writes:

- `codex_exec.log`: combined command logs/stdout/stderr.
- `changes.patch`: `git diff` output after execution.
- `execution_summary.json`: optional structured execution metadata.
