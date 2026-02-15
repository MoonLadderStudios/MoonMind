# Docker Image Notes

## Multi-stage Tooling Builder

The `api_service/Dockerfile` uses a dedicated Node.js builder stage named `tooling-builder` to install Spec Kit and Codex CLIs. The stage accepts two build arguments:

- `CODEX_CLI_VERSION` (default `latest`)
- `SPEC_KIT_VERSION` (default `0.4.0`)

During the build the stage:

1. Installs minimal Debian packages required for global npm installs.
2. Disables npm analytics prompts for deterministic builds.
3. Runs `npm install -g @openai/codex@${CODEX_CLI_VERSION}` and `npm install -g @githubnext/spec-kit@${SPEC_KIT_VERSION}`.
4. Cleans the npm cache before handing control back to the Python runtime stage.

The final runtime image copies only the produced binaries, supporting node modules, and licenses from the builder. Python remains the only runtime dependency while both CLIs are available on the default `PATH` for Celery workers and FastAPI.

### Updating Versions

Override the defaults when building an image:

```bash
docker build \
  --build-arg CODEX_CLI_VERSION=latest \
  --build-arg SPEC_KIT_VERSION=0.4.1 \
  -f api_service/Dockerfile .
```

Publishing a new image should always document the CLI versions in release notes so downstream operators can audit automation changes.
