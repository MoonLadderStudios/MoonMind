# Research: Remote Worker Daemon (015 Alignment)

## Decision 1: Startup preflight now enforces Speckit capability

- **Decision**: Extend daemon preflight to verify `speckit` executable and run `speckit --version` before claim loop start.
- **Rationale**: 015 umbrella requires Speckit to be present across worker runtimes, not only Celery workers.
- **Alternative rejected**: Deferred check after claim was rejected because it allows unready workers to claim jobs.

## Decision 2: Embedding readiness check is part of startup gate

- **Decision**: Add an embedding profile check in preflight: if `DEFAULT_EMBEDDING_PROVIDER=google`, require `GOOGLE_API_KEY` or `GEMINI_API_KEY`.
- **Rationale**: Fast-path runtime should fail with actionable diagnostics when Google embedding prerequisites are absent.
- **Alternative rejected**: Treating this as a warning was rejected because it delays failures into job execution.

## Decision 3: `codex_skill` adopts skills-first compatibility mapping

- **Decision**: Remote worker now accepts `codex_skill` claims and maps them into deterministic `codex_exec` compatibility payloads via handler translation.
- **Rationale**: Supports umbrella skills-first migration without requiring a full skill-definition execution engine in this milestone.
- **Alternative rejected**: Keeping `codex_skill` unsupported was rejected because it conflicts with umbrella direction.

## Decision 4: Skill allowlist policy is local worker config

- **Decision**: Add `default_skill` and `allowed_skills` to worker config with env-driven defaults (`MOONMIND_DEFAULT_SKILL`, `MOONMIND_ALLOWED_SKILLS`, fallback to `SPEC_WORKFLOW_*`).
- **Rationale**: Worker-level guardrails are needed so unsupported skills fail before execution.
- **Alternative rejected**: Relying only on server-side policy was rejected because runtime observability loses local intent.

## Decision 5: Event payloads include execution metadata

- **Decision**: Emitted worker events include `selectedSkill`, `executionPath`, `usedSkills`, `usedFallback`, `shadowModeRequested`.
- **Rationale**: Brings remote worker observability into parity with umbrella skills-first telemetry expectations.
- **Alternative rejected**: Summary-only event text was rejected as insufficient for path diagnostics.

## Validation Notes

- Scope validation helper script `.specify/scripts/bash/validate-implementation-scope.sh` is not present in this repository.
- Unit validation remains enforceable through `./tools/test_unit.sh`.
