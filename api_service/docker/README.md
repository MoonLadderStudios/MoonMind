# Docker Image Notes

`api_service/Dockerfile` builds the shared API and Temporal worker image. Agent
CLIs and provider SDKs are owned by Omnigent and its host image at
`services/omnigent/moonmind-host/Dockerfile`; the application image does not
install Codex, Claude Code, OpenCode, Gemini, or Node/npm.

The frontend builder uses Node to compile Vite assets. Production receives only
the compiled assets. The `test-runtime` target includes Node/npm separately so
`tools/test_unit.sh` can run both Python and frontend tests. Its Python test
extras include the TOML parser used by dependency checks.

The production Python graph is exported from `poetry.lock` with `--only main`.
Keep HTTP/WebSocket support, PostgreSQL drivers, artifact storage, Docker access,
and telemetry declared for their application consumers. OAuth enrollment runs
provider CLIs in the installed Omnigent host image; an explicit
`MOONMIND_OAUTH_RUNNER_IMAGE` overrides that selection. API and worker startup do
not read or rewrite local agent configuration or credential homes.

The historical Codex login-check operation also selects the shared Omnigent
host image, with `CODEX_LOGIN_CHECK_IMAGE` retained as its explicit override.

The application image retains Git, GitHub CLI, Docker CLI/Buildx/Compose,
ripgrep, curl, SSH, and process tools for workspace publication, managed
container jobs, deployment coordination, and diagnostics. The standalone
queue-worker entrypoint has been removed; durable execution belongs to Temporal.
