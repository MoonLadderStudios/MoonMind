# Research: Dual OAuth Setup for Codex + Claude with Default Task Runtime

## Decision 1: Add dedicated Claude auth volume configuration

- **Decision**: Introduce `CLAUDE_VOLUME_NAME`, `CLAUDE_VOLUME_PATH`, and `CLAUDE_HOME` environment settings and wire the volume into worker services.
- **Rationale**: Mirrors existing Codex persistence model and enables restart-safe OAuth state for Claude without requiring API keys.
- **Alternatives considered**:
  - Reuse Codex auth volume path: rejected due cross-tool config coupling and risk of incompatible directory layouts.
  - Use ephemeral container filesystem only: rejected because OAuth state would be lost after restart.

## Decision 2: Use a dedicated `auth-claude-volume.sh` helper script

- **Decision**: Add `tools/auth-claude-volume.sh` with a login flow followed by explicit auth-status verification in the worker container context.
- **Rationale**: Matches current operator ergonomics for Codex and supports the requested two-command setup model.
- **Alternatives considered**:
  - One combined setup command: rejected because user explicitly requested separate simple commands.
  - Manual documentation-only steps: rejected due higher operator error rate and lower repeatability.

## Decision 3: Runtime-specific preflight auth enforcement

- **Decision**: Extend worker CLI preflight to require auth checks by runtime mode:
  - `codex`: check Codex login status
  - `claude`: check Claude auth status
  - `universal`: check both
- **Rationale**: Keeps failures actionable and avoids blocking one runtime due missing credentials for another.
- **Alternatives considered**:
  - Always check both in all modes: rejected as too strict and breaks codex-only/claude-only workers.
  - Skip Claude auth check entirely: rejected because it permits runtime failures after claims begin.

## Decision 4: Configurable default task runtime in queue normalization path

- **Decision**: Add `default_task_runtime` setting (env alias `MOONMIND_DEFAULT_TASK_RUNTIME`) and apply it only when incoming payload omits runtime.
- **Rationale**: Meets operator requirement for configurable defaults while preserving explicit task runtime precedence.
- **Alternatives considered**:
  - Keep hardcoded `codex`: rejected because it cannot support a Claude-first deployment.
  - Force runtime on every submit: rejected because existing clients depend on server defaults.
