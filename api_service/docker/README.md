# Docker Image Notes

## Multi-stage Tooling Builder

The `api_service/Dockerfile` uses a dedicated Node.js builder stage named `tooling-builder` to install the packaged CLIs used by the shared runtime image.

Relevant build args include:

- `CODEX_CLI_VERSION`
- `GEMINI_CLI_VERSION`
- `CLAUDE_CLI_VERSION`

During the build the stage:

1. Installs minimal Debian packages required for global npm installs.
2. Disables npm analytics prompts for deterministic builds.
3. Runs `npm install -g` for the requested CLI packages.
4. Replaces the npm-created `codex` and `gemini` launcher symlinks with stable wrapper scripts so multi-stage `COPY` keeps those CLIs anchored in the copied `node_modules` tree.
5. Cleans the npm cache before handing control back to the Python runtime stage.

The final runtime image copies the produced launchers, the Node runtime, supporting `node_modules`, and license files from the builder. It intentionally relies on the platform-specific optional dependency already installed under `@openai/codex` rather than copying an extra Codex vendor tree into the image.

The runtime stage also includes additional shell tooling required by bootstrap and diagnostics workflows, including `rg` (`ripgrep`) so Codex preflight and maintenance shells can perform reliable search checks without ad-hoc runtime installs.

### Updating Versions

Override the defaults when building an image:

```bash
docker build \
  --build-arg CODEX_CLI_VERSION=0.104.0 \
  --build-arg GEMINI_CLI_VERSION=latest \
  --build-arg CLAUDE_CLI_VERSION=latest \
  -f api_service/Dockerfile .
```

Publishing a new image should always document the CLI versions in release notes so downstream operators can audit automation changes.
