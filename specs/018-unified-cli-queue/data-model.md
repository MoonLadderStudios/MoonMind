# Data Model: Unified CLI Single Queue Worker Runtime

## Entity: WorkerRuntimeMode

- Description: Runtime mode selected at worker startup.
- Type: Enumerated string.
- Allowed Values:
  - `codex`
  - `gemini`
  - `claude`
  - `universal`
- Validation Rules:
  - Missing value defaults to `codex`.
  - Unknown values are invalid and must fail startup.

## Entity: RuntimeNeutralJob

- Description: Queue payload executable by any runtime worker.
- Core Fields:
  - `job_id`: unique job identifier.
  - `repo`: repository location and revision metadata.
  - `workdir`: relative workspace path.
  - `task.goal`: objective statement.
  - `task.constraints[]`: explicit execution constraints.
  - `task.inputs`: required artifacts.
  - `task.outputs`: expected deliverables.
  - `runtime_hints`: optional non-binding runtime hints.
  - `task.target_runtime` (optional): requested runtime for universal routing.
- Validation Rules:
  - Base payload must be valid without runtime-specific command strings.
  - `task.target_runtime`, when present, must be one of the runtime enum values excluding unsupported aliases.

## Entity: RunnerBinding

- Description: Mapping from `WorkerRuntimeMode` to concrete runtime runner implementation.
- Fields:
  - `mode`: runtime mode enum.
  - `runner_name`: implementation (`CodexRunner`, `GeminiRunner`, `ClaudeRunner`, `UniversalRunner`).
  - `healthy`: startup readiness result.
- State Transitions:
  - `unresolved` -> `validated` when env + CLI checks pass.
  - `unresolved` -> `failed` on invalid env mode or missing CLI.

## Entity: QueueBinding

- Description: Effective worker queue configuration.
- Fields:
  - `default_queue`: expected queue (`moonmind.jobs`).
  - `legacy_queue_overrides`: optional legacy queue env values detected.
  - `effective_queue`: final queue for worker binding.
- Rules:
  - `effective_queue` should be `moonmind.jobs` for steady-state runtime.
  - Legacy queue values are transitional and tracked for deprecation.

## Entity: CliHealthSnapshot

- Description: Startup check result across bundled CLIs.
- Fields:
  - `codex_ok`
  - `gemini_ok`
  - `claude_ok`
  - `speckit_ok`
  - `status`
  - `message`
- Rule:
  - Worker readiness requires all required checks to pass.
