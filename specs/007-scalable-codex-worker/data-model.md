# Data Model: Scalable Codex Worker (015-Aligned)

## Value Object: CodexWorkerStartupProfile

Captures startup readiness for the Codex worker process.

- **Fields**:
  - `worker_name` (`codex`)
  - `queues` (tuple[str, ...])
  - `codex_preflight_status` (`passed` | `failed` | `skipped`)
  - `speckit_cli_available` (bool)
  - `embedding_provider` (str)
  - `embedding_model` (str)
  - `embedding_credential_source` (`google_api_key` | `gemini_api_key` | null)
- **Validation rules**:
  - if `embedding_provider=google`, credential source must be non-null.
  - worker readiness requires successful Codex preflight.

## Value Object: GeminiWorkerStartupProfile

Captures startup readiness for the Gemini worker process.

- **Fields**:
  - `worker_name` (`gemini`)
  - `queues` (tuple[str, ...])
  - `gemini_cli_available` (bool)
  - `speckit_cli_available` (bool)
  - `embedding_provider` (str)
  - `embedding_model` (str)
  - `embedding_credential_source` (`google_api_key` | `gemini_api_key` | null)
- **Validation rules**:
  - if `embedding_provider=google`, credential source must be non-null.
  - Gemini worker startup requires an API credential (`GEMINI_API_KEY` or `GOOGLE_API_KEY`).

## Value Object: StageExecutionDecision

Represents policy output before stage execution.

- **Fields**:
  - `stage_name` (str)
  - `selected_skill` (str)
  - `execution_path` (`skill` | `direct_fallback` | `direct_only`)
  - `use_skills` (bool)
  - `fallback_enabled` (bool)
  - `shadow_mode` (bool)

## Value Object: StageExecutionOutcome

Represents stage execution result metadata.

- **Fields**:
  - `stage_name` (str)
  - `selected_skill` (str)
  - `execution_path` (`skill` | `direct_fallback` | `direct_only`)
  - `used_skills` (bool)
  - `used_fallback` (bool)
  - `shadow_mode_requested` (bool)
  - `result` (Any)

## Persisted Payload Shape: WorkflowTaskStatePayload

For discover/submit/publish task-state payloads.

- **Fields**:
  - `status`
  - `message`
  - stage-specific task fields (`taskId`, `logsPath`, `patchPath`, etc.)
  - `selectedSkill`
  - `executionPath`
  - `usedSkills`
  - `usedFallback`
  - `shadowModeRequested`

## State Transitions

1. `queued` -> `running` when a stage task starts.
2. `running` -> `succeeded` on successful completion.
3. `running` -> `failed` on terminal failure.
4. `running` -> `skipped` for no-op/no-work paths.
5. During `running`, execution metadata path transitions can be:
   - `skill`
   - `direct_fallback` (when adapter fails and fallback is enabled)
   - `direct_only` (skills policy bypassed)
