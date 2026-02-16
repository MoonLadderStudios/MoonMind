# Data Model: Worker GitHub Token Authentication Fast Path

## Value Object: GitAuthStartupContext

Represents startup-time GitHub auth inputs and preflight outcomes.

- **Fields**:
  - `github_token_present` (bool)
  - `gh_binary_path` (string)
  - `auth_setup_attempted` (bool)
  - `auth_status_ok` (bool)
  - `failure_reason` (string | null)
- **Validation rules**:
  - `gh_binary_path` required when token is present and auth setup is attempted.
  - `auth_status_ok` must be true before worker poll loop starts when token is present.

## Value Object: CodexExecRepositoryInput

Normalized repository input accepted by `codex_exec` payload handling.

- **Fields**:
  - `raw_value` (string)
  - `normalized_clone_target` (string)
  - `input_kind` (`slug` | `https` | `ssh`)
  - `contains_embedded_credentials` (bool)
- **Validation rules**:
  - Empty repository value is invalid.
  - HTTPS URLs with userinfo credentials are invalid.
  - Slug inputs normalize to `https://github.com/<slug>.git`.

## Value Object: WorkerCommandLogRecord

Represents one command/log entry emitted during handler execution.

- **Fields**:
  - `command_text` (string)
  - `stdout_excerpt` (string)
  - `stderr_excerpt` (string)
  - `redaction_applied` (bool)
- **Validation rules**:
  - Redaction must be applied when known sensitive values are present.
  - Stored log text must not contain raw `GITHUB_TOKEN` values.

## State Rules

- Startup preflight order: verify `codex` -> verify Codex login -> verify `gh` (when needed) -> configure `gh auth login/setup-git` (when token exists) -> verify `gh auth status`.
- Startup failure on any auth-preflight error must occur before `run_once`/`run_forever` polling.
- Repository input validation must occur before clone command dispatch.
