# MoonMind Release Notes

## 2025-11-05 – Codex & Spec Kit Tooling Availability

### Highlights
- Bundled Codex and Spec Kit CLIs into the shared `api_service` image and validated availability via Docker build and runtime health checks.
- Added a docker-compose smoke target to assert `codex --version` and `speckit --version` succeed during CI runs.
- Documented fast-reference troubleshooting guidance and quickstart verification outputs for operations.

### Bundled CLI Versions
- `@githubnext/codex-cli` (`codex`) – **0.6.0** (controlled via `CODEX_CLI_VERSION` build arg).
- `@githubnext/spec-kit` (`speckit`) – **0.4.0** (controlled via `SPEC_KIT_VERSION` build arg).

### Upgrade Guidance
- Bump the corresponding build arguments and rerun `docker compose run --rm cli-tooling-smoke` to ensure both CLIs report the expected versions before promoting images.
- Capture updated quickstart verification logs in `specs/004-install-codex-spec/research.md#verification` when versions change.
