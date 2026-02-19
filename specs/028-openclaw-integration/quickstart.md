# OpenClaw Integration Quickstart

## Prerequisites
- Docker + Docker Compose v2
- Codex CLI logged in for the primary `codex_worker` volume (`tools/auth-codex-volume.sh`)
- `.env` populated with OpenClaw variables:
  - `OPENCLAW_MODEL=gpt-5.3-codex`
  - `OPENCLAW_MODEL_LOCK_MODE=force`
  - `OPENCLAW_CODEX_VOLUME_NAME=openclaw_codex_auth_volume`
  - `OPENCLAW_CODEX_VOLUME_PATH=/home/app/.codex`

## Steps
1. **Bootstrap the dedicated auth volume**
   ```bash
   ./tools/bootstrap-openclaw-codex-volume.sh
   ```
   - Ensures `local-network` exists, clones from `codex_auth_volume`, and validates `codex login status` inside the OpenClaw container.
2. **Start the service (profile-gated)**
   ```bash
   docker compose --profile openclaw up -d openclaw
   ```
3. **Verify model lock enforcement**
   - Tail logs: `docker compose logs openclaw -f | grep model`
   - Run an OpenClaw action that attempts to override the model; expect either a warning (`force`) or immediate rejection (`reject`).
4. **Update credentials**
   - Re-run the bootstrap script whenever the main Codex credentials rotate.
5. **Shut down (optional)**
   ```bash
   docker compose --profile openclaw down
   ```

## Troubleshooting
- **Missing source volume**: Run `tools/auth-codex-volume.sh` first or authenticate manually.
- **Volume busy**: Stop any containers using `openclaw_codex_auth_volume` before cloning.
- **`codex login status` fails**: Manually enter the container using `docker compose --profile openclaw run --rm --user app openclaw bash` and run `codex login --device-auth`.
