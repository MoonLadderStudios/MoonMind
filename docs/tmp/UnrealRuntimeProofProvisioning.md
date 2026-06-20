# Unreal (Tactics) Runtime Proof provisioning

Rollout note for enabling managed-worker build/test ("Runtime Proof") of the
`MoonLadderStudios/Tactics` Unreal Engine project. This is migration/operational
guidance, not canonical desired-state docs.

## Why this exists

Workflow `mm:02c36f8a-0eb8-41ea-b3da-456ecdd0fa50` (Jira Implement, THOR-555)
failed after 3h10m, blocked at the validation step. The agent had no native
UE/PowerShell toolchain, so its only validation path was the Tactics
`tactics-test` skill (`run_dood_unreal_tactics.sh`), which pulls a UE toolchain
image into the per-session Docker (DinD) sidecar and builds/tests inside it.

The pull never succeeded because:

1. The skill defaulted to the **EULA-gated** `ghcr.io/epicgames/unreal-engine:dev-5.7`,
   pullable only with an Epic-linked GHCR token (the repo's `UNREAL_ENGINE_PAT`).
2. MoonMind's session GHCR auth (`resolve_ghcr_pull_credentials_for_launch`) reads
   `GHCR_PULL_USER` / `GHCR_PULL_TOKEN`; neither was configured, so pulls were
   anonymous → `unauthorized` (and, on a sidecar without working egress, a hang).
3. The docker-sidecar preflight that can `docker manifest inspect` and fail fast was
   inert because no pinned UE image ref was configured.

## What changed in code/config (already applied)

- **`.agents/skills/tactics-test/scripts/run_dood_unreal_tactics.sh`** (Tactics repo):
  default runner image is now the org-published prebuilt
  `ghcr.io/moonladderstudios/tactics-ue-base:latest` (what CI uses), overridable via
  `--image`, `MOONMIND_UNREAL_ENGINE_IMAGE`, or `UE_RUNNER_IMAGE`. The image pull is
  now visible and bounded by `MOONMIND_UE_PULL_TIMEOUT` (default 1800s); a stalled or
  unauthorized pull fails fast with a clear gate reason instead of hanging.
- **`docker/sandbox-egress-proxy/squid.conf`**: added `.ghcr.io` to the egress
  allowlist so sidecars on the restricted egress network can reach the registry.
- **`docker-compose.yaml`** (`temporal-worker-agent-runtime`): pass through
  `GHCR_PULL_USER`, `GHCR_PULL_TOKEN`, `MOONMIND_UNREAL_ENGINE_IMAGE`, and
  `MOONMIND_DOCKER_PREFLIGHT_IMAGE_REF` (default empty → unchanged behavior).

## What an operator must still do

These need real secret values / a verified digest and cannot be committed:

1. **Provision GHCR pull credentials** with `read:packages` for
   `moonladderstudios/tactics-ue-base`. Either set in `.env`:
   ```
   GHCR_PULL_USER=<github-username-or-bot>
   GHCR_PULL_TOKEN=<PAT with read:packages>
   ```
   or register `GHCR_PULL_USER` / `GHCR_PULL_TOKEN` as managed secrets, or supply
   `GHCR_PULL_USER_SECRET_REF` / `GHCR_PULL_TOKEN_SECRET_REF`. The existing
   `GITHUB_TOKEN` is **not** sufficient (probe returned 403) and is not wired into
   the GHCR pull path.

2. **Pin the preflight/runner image to a digest** and set it for the worker:
   ```
   # obtain once, with a token that can read the package:
   docker manifest inspect ghcr.io/moonladderstudios/tactics-ue-base:latest \
     --verbose | sha256-of-the-manifest
   MOONMIND_UNREAL_ENGINE_IMAGE=ghcr.io/moonladderstudios/tactics-ue-base@sha256:<digest>
   ```
   The preflight rejects unpinned `:latest`, so a digest (or non-latest tag) is
   required there. With this set, a session that cannot pull the image aborts in
   seconds with `pinned UE image manifest could not be inspected … unauthorized`
   instead of failing 3h later inside the agent.

3. If sidecars run on the locked-down `sandbox-egress-network`, also ensure the DinD
   daemon uses the egress proxy (the squid allowlist now permits `ghcr.io`, but the
   daemon must be told to use it). On the default `local-network` (NAT egress) this is
   not required.

## Deferred (separate change, needs design + tests)

- **Shared/persistent sidecar image cache.** The DinD sidecar uses a per-session
  graph volume (`_sidecar_graph_volume_name(session_id)`), so the multi-GB UE image is
  re-pulled every run. Moving to a shared/repo-scoped graph volume (or pre-seeding the
  image) changes isolation posture and needs workflow/activity-boundary tests.
- **Preset terminal-state semantics.** Jira Implement converts a `FAIL` validation gate
  into a workflow-level `ApplicationError` (blocked), so an unprovisioned validation
  environment reads as a hard failure. Consider a distinct "validation environment
  unavailable" terminal state. Touches the workflow contract → requires boundary/replay
  coverage per the repo testing rules.
