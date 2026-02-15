# MoonMind Release Notes

## 2025-11-30 – Gemini CLI Availability

### Highlights
- Added the official `@google/gemini-cli` to the shared `api_service` image for orchestrator and Celery worker containers.
- Documented verification guidance and troubleshooting for missing API keys or network access when invoking `gemini`.
- Captured upgrade cues alongside existing Codex/Spec Kit tooling to keep CLI versions consistent across environments.

### Bundled CLI Versions
- `@google/gemini-cli` (`gemini`) – **latest** by default (controlled via `GEMINI_CLI_VERSION` build arg).

### Upgrade Guidance
- Bump `GEMINI_CLI_VERSION` and rebuild the `api_service` image; rerun the Gemini verification steps in `specs/006-add-gemini-cli/research.md#verification` to confirm availability.

## 2025-11-05 – Codex & Spec Kit Tooling Availability

### Highlights
- Bundled Codex and Spec Kit CLIs into the shared `api_service` image and validated availability via Docker build and runtime health checks.
- Added a docker-compose smoke target to assert `codex --version` and `speckit --version` succeed during CI runs.
- Documented fast-reference troubleshooting guidance and quickstart verification outputs for operations.

### Bundled CLI Versions
- `@openai/codex` (`codex`) – **latest** (controlled via `CODEX_CLI_VERSION` build arg).
- `@githubnext/spec-kit` (`speckit`) – **0.4.0** (controlled via `SPEC_KIT_VERSION` build arg).

### Upgrade Guidance
- Bump the corresponding build arguments and rerun `docker compose run --rm cli-tooling-smoke` to ensure both CLIs report the expected versions before promoting images.
- Capture updated quickstart verification logs in `specs/004-install-codex-spec/research.md#verification` when versions change.
