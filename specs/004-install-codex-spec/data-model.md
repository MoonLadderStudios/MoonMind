# Data Model: Codex & Spec Kit Tooling Availability

## Entities

### RuntimeImageToolchain
Represents the shared `api_service` container image that packages Codex CLI, GitHub Spec Kit CLI, and the managed Codex config template used by Celery workers.

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `base_image` | string | Upstream image tag (e.g., `python:3.11-slim`) used before customizations | Must be an approved distro tag maintained by security team |
| `node_version` | string | Node.js version installed in the build stage to support npm CLIs | Must be >= 18.x LTS |
| `codex_cli_version` | string | Semver string injected via `CODEX_CLI_VERSION` build arg | Cannot be empty; must match `npm view @githubnext/codex-cli versions` |
| `spec_kit_version` | string | Semver string for `@githubnext/spec-kit` CLI | Same validation as above |
| `codex_config_template_path` | path | Location of the baked template (e.g., `/etc/codex/config.toml`) | File must exist in final layer and be readable by worker UID |
| `install_logs` | text array | Captured logs/hashes proving CLI install + checksum | Must include at least `codex --version` and `speckit --version` outputs |

### CodexConfigProfile
Represents the effective `~/.codex/config.toml` managed for the Celery worker user.

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `path` | path | Absolute path to the profile within the worker home directory | Must resolve under `$HOME/.codex/config.toml` |
| `owner_uid` | int | UID that owns the config | Matches Celery worker UID (default 1000) |
| `owner_gid` | int | GID that owns the config | Matches Celery worker GID |
| `approval_policy` | enum | Must be `"never"` for non-interactive runs per spec | Hard-coded to `never`; failing values block health checks |
| `extra_settings` | map | All other Codex CLI preferences preserved from existing file | Merge script must keep keys/values verbatim |
| `managed_on` | timestamp | Last time the merge script enforced policy | Updated at container start; used for auditing |

### WorkerToolingHealthCheck
Captures the verification state that workers log/emit after validating the packaged CLIs and config.

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `worker_name` | string | Celery worker identifier (e.g., `codex-0`) | Non-empty; matches Celery config |
| `codex_cli_status` | enum | `passed`, `failed`, or `skipped` result of `codex --version && codex login status` | Must be `passed` before worker accepts jobs |
| `spec_kit_status` | enum | Result of `speckit --help` smoke test | Same as above |
| `config_hash` | string | Hash of the resolved `~/.codex/config.toml` to detect drift | Recomputed whenever config changes |
| `last_checked_at` | timestamp | When the health check last ran | Cannot be null; used to enforce freshness |
| `failure_message` | string | Detailed log when either CLI/config check fails | Required when statuses are `failed` |

## Relationships

1. **RuntimeImageToolchain ➝ CodexConfigProfile**: One runtime image defines the config template applied to every worker profile derived from that image.
2. **RuntimeImageToolchain ➝ WorkerToolingHealthCheck**: Each worker running on the image reports tooling health, allowing operators to trace failures back to the specific image version.
3. **CodexConfigProfile ➝ WorkerToolingHealthCheck**: A health check references the config hash from the profile to prove the enforced approval policy matches expectations.

## Validation & State Transitions

- **Image Promotion**: `RuntimeImageToolchain` transitions from `built` → `verified` once CLI install logs exist and automated smoke tests pass. Only `verified` images can be tagged for deployment.
- **Config Enforcement**: `CodexConfigProfile` enters `managed` after the merge script runs successfully. If a worker detects manual edits removing `approval_policy = "never"`, it reverts to `drifted` and triggers remediation instructions.
- **Worker Admission Control**: `WorkerToolingHealthCheck` must report `passed` statuses for both CLIs before the worker process registers with Celery queues. Failure keeps the worker in `quarantined` state until remediation.
- **Auditing**: Whenever `codex_cli_version` or `spec_kit_version` changes, all downstream `WorkerToolingHealthCheck` entries must be refreshed to guarantee workers picked up the new binaries.
