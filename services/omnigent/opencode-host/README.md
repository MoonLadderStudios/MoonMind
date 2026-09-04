# omnigent-host-opencode

Dedicated harness-specific Omnigent host image for OpenCode Go.

- Base: same immutable `ghcr.io/omnigent-ai/omnigent-host` revision as MoonMind's stock host.
- Adds only `opencode-ai@1.18.11` (≈ `~1.18.0`) via `npm install -g` at **build time**.
- Build fails when `opencode --version` is outside `>=1.17.7,<1.19.0`.
- Warms the OpenCode plugin SDK closure (`@opencode-ai/plugin@1.18.11`) into
  `/opt/moonmind/opencode-npm-cache` and fails the build unless a second
  install resolves from that cache with `--offline`.
- No per-workflow `npm install` — container starts immediately with the CLI present,
  and OpenCode's own first-start plugin install resolves from the warm cache.

## Build

```bash
# Pinned base digest (from OMNIGENT_HOST_IMAGE_REF)
export BASE_DIGEST="sha256:..."
export BASE_IMAGE="ghcr.io/omnigent-ai/omnigent-host@${BASE_DIGEST}"

docker build \
  --build-arg OMNIGENT_HOST_BASE_IMAGE="${BASE_IMAGE}" \
  --build-arg OPENCODE_VERSION=1.18.11 \
  -t ghcr.io/moonladderstudios/omnigent-host-opencode:1.18.11 \
  -f services/omnigent/opencode-host/Dockerfile .
```

Multi-arch:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg OMNIGENT_HOST_BASE_IMAGE="${BASE_IMAGE}" \
  -t ghcr.io/moonladderstudios/omnigent-host-opencode:1.18.11 \
  -f services/omnigent/opencode-host/Dockerfile \
  --push .
```

The CI workflow `docker-publish-opencode-host.yml` builds and publishes a digest-pinned manifest list with provenance.

## Verification

```bash
docker run --rm ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest> opencode --version
docker run --rm ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest> sh -c 'command -v opencode && ls -l /usr/local/bin/opencode'
```

Expected: `opencode 1.18.11` (or compatible `1.18.x`), binary present, no unrelated harness CLIs introduced beyond base.

Prove the image starts `opencode serve` for a plugin-enabled session without the network:

```bash
sh services/omnigent/opencode-host/verify-warm-plugin-cache.sh \
  ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest>
```

The check mirrors the MoonMind entrypoint seam (cache copied into
`$HOME/.omnigent/moonmind/opencode-npm-cache`, `$HOME/.npmrc` pointing at it)
inside a fresh tmpfs home with `--network none`. The release workflow runs it
against every built digest before publishing.

## Operator configuration

Set in `.env` or deployment environment:

```
OMNIGENT_OPENCODE_HOST_IMAGE_REF=ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest>
```

The Host Class `omnigent-opencode@1` is bootstrapped from this digest. Mutable tags fail closed in production.

## Cache behavior

The image reuses already-cached base layers (`omnigent-host`) plus the OpenCode layer. It is pulled lazily — only when an OpenCode workflow requires it — and reused across later runs.

OpenCode installs `@opencode-ai/plugin@<opencode version>` with `npm install`
inside every fresh per-session config directory the first time `opencode
serve` starts. The image ships that exact dependency closure in
`/opt/moonmind/opencode-npm-cache` (label
`moonmind.opencode.plugin_npm_cache`). MoonMind's host entrypoint copies the
seed into the run-owned state volume and writes `$HOME/.npmrc` so the install
is served from the cache; see `docs/Omnigent/OpenCodeHost.md`.
