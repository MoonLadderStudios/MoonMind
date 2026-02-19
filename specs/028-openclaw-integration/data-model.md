# Data / Configuration Model

Even though this feature is infra-heavy, we model the key configuration entities to keep compose, env vars, and scripts aligned.

## Entity: OpenClawServiceConfig
- **Description**: Compose/entrypoint definition for the optional OpenClaw container.
- **Fields**:
  - `service_name` (string, default `openclaw`)
  - `profiles` (array, must include `openclaw`)
  - `depends_on` (`api` service alias)
  - `networks` (must include `local-network`)
  - `volumes` (`openclaw_codex_auth_volume` → `${OPENCLAW_CODEX_VOLUME_PATH}`, `openclaw_data` → `/var/lib/openclaw`)
  - `environment` ( `OPENCLAW_MODEL`, `OPENCLAW_MODEL_LOCK_MODE`, `CODEX_*`, `MOONMIND_URL`, `MOONMIND_API_TOKEN` )
  - `user` (`app`)
  - `restart_policy` (`unless-stopped`)

## Entity: OpenClawCodexVolume
- **Description**: Dedicated Docker volume for Codex OAuth artifacts.
- **Fields**:
  - `name` (`${OPENCLAW_CODEX_VOLUME_NAME:-openclaw_codex_auth_volume}`)
  - `mount_path` (`${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}`)
  - `initialization_strategy` (`clone` via bootstrap script or `manual` login)
  - `validation_command` (`codex login status` executed inside OpenClaw container)
  - `ownership` (`app` user inside container; root on host Docker daemon)

## Entity: ModelLockPolicy
- **Description**: Guardrail controlling whether override attempts are forced or rejected.
- **Fields**:
  - `model` (`OPENCLAW_MODEL`, required, non-empty)
  - `mode` (`OPENCLAW_MODEL_LOCK_MODE` enum: `force`, `reject`)
  - `adapter_path` (`openclaw/llm.py` module)
  - `wrapper_path` (`services/openclaw/bin/codex`, optional)
  - `logging_channel` (structured logger name, e.g., `openclaw.llm.policy`)
  - `metric_tags` (`model`, `mode`, `result` for StatsD counters)

## Entity: BootstrapScriptConfig
- **Description**: Parameters that control `tools/bootstrap-openclaw-codex-volume.sh` execution.
- **Fields**:
  - `network_name` (`${MOONMIND_DOCKER_NETWORK:-local-network}`)
  - `source_volume` (`${CODEX_VOLUME_NAME:-codex_auth_volume}`)
  - `destination_volume` (`${OPENCLAW_CODEX_VOLUME_NAME:-openclaw_codex_auth_volume}`)
  - `service_profile` (`openclaw`)
  - `service_user` (`app` by default, override via `OPENCLAW_DOCKER_USER`)
  - `status_command` (`codex login status`)
  - `safety_checks` (ensure docker CLI present, source volume exists, destination not attached, validation step passes)
