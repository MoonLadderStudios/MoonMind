# Data Model: Remote Worker Daemon (015-Aligned)

## Value Object: RemoteWorkerStartupProfile

Startup preflight diagnostics for standalone worker runtime.

- `codex_cli_available` (bool)
- `speckit_cli_available` (bool)
- `codex_login_status` (`passed` | `failed`)
- `embedding_provider` (str)
- `embedding_model` (str)
- `embedding_credentials_available` (bool)

Validation rules:
- startup readiness requires `codex_cli_available=true`, `speckit_cli_available=true`, and `codex_login_status=passed`.
- when `embedding_provider=google`, `embedding_credentials_available` must be true.

## Value Object: RemoteWorkerSkillPolicy

Local skills policy resolved from environment.

- `default_skill` (str)
- `allowed_skills` (tuple[str, ...])
- `allowed_types` (tuple[str, ...]) including `codex_exec` and `codex_skill`

Validation rules:
- `default_skill` must always appear in `allowed_skills`.
- claimed `codex_skill` requests must have `skillId` in `allowed_skills`.

## Value Object: QueueExecutionMetadata

Execution metadata emitted in worker events.

- `selectedSkill` (str)
- `executionPath` (`skill` | `direct_fallback` | `direct_only`)
- `usedSkills` (bool)
- `usedFallback` (bool)
- `shadowModeRequested` (bool)

## Value Object: CodexSkillCompatibilityRequest

Normalized compatibility mapping from `codex_skill` to `codex_exec`.

- `skill_id` (str)
- `inputs` (dict[str, Any])
- `repository` (str)
- `instruction` (str)
- `ref` (str | null)
- `workdir_mode` (`fresh_clone` | `reuse`)
- `publish_mode` (`none` | `branch` | `pr`)
- `publish_base_branch` (str | null)

Validation rules:
- repository context is required (`inputs.repo` or equivalent fallback field).
- instruction may be synthesized from inputs when absent.

## Value Object: CodexRuntimeSelection

Resolved Codex runtime settings for one claimed task.

- `task_model` (str | null)
- `task_effort` (str | null)
- `worker_default_model` (str | null)
- `worker_default_effort` (str | null)
- `resolved_model` (str | null)
- `resolved_effort` (str | null)

Validation rules:
- If `task_model` exists, it overrides worker/default model selection for that task.
- If `task_effort` exists, it overrides worker/default effort selection for that task.
- If task fields are missing, resolved values fall back to worker defaults; if worker defaults are missing, Codex CLI defaults apply.

## Existing Entity: WorkerExecutionResult

Handler output used by daemon terminal transitions.

- `succeeded` (bool)
- `summary` (str | null)
- `error_message` (str | null)
- `artifacts` (tuple[ArtifactUpload, ...])

## State Transitions

1. Job is claimed (`queued` -> `running`) with local execution metadata computed.
2. Handler path is selected:
   - `codex_exec` -> `direct_only`
   - `codex_skill` + `skillId=speckit` -> `skill`
   - `codex_skill` + allowlisted non-speckit -> `direct_fallback`
3. Artifacts upload (best effort per artifact) while job remains running.
4. Terminal transition:
   - success -> `succeeded`
   - failure -> `failed`
