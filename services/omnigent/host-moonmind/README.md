# omnigent-host-moonmind

Neutral shared MoonMind Omnigent host image for Codex, Claude Code, and OpenCode.

- Base: immutable `ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>`
- Adds `opencode-ai@1.18.11`, `@openai/codex@0.52.0`, `@anthropic-ai/claude-code@2.1.257` at **build time** via `npm install -g`.
- Build fails when any version is outside its supported range:
  - opencode `>=1.17.7,<1.19.0`
  - codex `>=0.50.0,<1.0.0`
  - claude `>=2.0.0,<3.0.0`
- No per-workflow `npm install` — container starts immediately with all CLIs present.
- Runs as `1000:1000`, `HOME=/home/app`, `WORKDIR=/home/app`.
- Carries `moonmind.omnigent.build_digest` label plus `moonmind.*.version` labels.
- Alias `ghcr.io/moonladderstudios/omnigent-host-opencode` → same manifest digest while alias contract is active.

## Build

```bash
export BASE_DIGEST="sha256:..."
export BASE_IMAGE="ghcr.io/omnigent-ai/omnigent-host@${BASE_DIGEST}"

docker build \
  --build-arg OMNIGENT_HOST_BASE_IMAGE="${BASE_IMAGE}" \
  --build-arg OMNIGENT_BUILD_DIGEST="${BASE_DIGEST}" \
  --build-arg OPENCODE_VERSION=1.18.11 \
  --build-arg CODEX_VERSION=0.52.0 \
  --build-arg CLAUDE_VERSION=2.1.257 \
  -t ghcr.io/moonladderstudios/omnigent-host-moonmind:1.18.11 \
  -f services/omnigent/host-moonmind/Dockerfile .
```

Multi-arch:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg OMNIGENT_HOST_BASE_IMAGE="${BASE_IMAGE}" \
  -t ghcr.io/moonladderstudios/omnigent-host-moonmind:1.18.11 \
  -f services/omnigent/host-moonmind/Dockerfile \
  --push .
```

CI workflow `docker-publish-omnigent-host-moonmind.yml` builds and publishes a digest-pinned manifest list with SBOM/provenance and also publishes the legacy `omnigent-host-opencode` alias to same digest.

## Verification

```bash
docker run --rm ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest> omnigent --version
docker run --rm ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest> codex --version
docker run --rm ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest> claude --version
docker run --rm ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest> opencode --version
docker run --rm ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest> sh -c 'command -v codex && command -v claude && command -v opencode && id && echo $HOME'
```

## Operator configuration (neutral preferred)

```
OMNIGENT_RUNTIME_HOST_IMAGE_REF=ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest>
```

Legacy alias (same digest) while migration requires it:

```
OMNIGENT_OPENCODE_HOST_IMAGE_REF=ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<same-digest>
```

Both names must resolve to same manifest digest while alias contract is active; config fails closed if they differ.

## Retirement condition

Alias `omnigent-host-opencode` may be retired when no active plan, Host Class `omnigent-opencode@1`, deployment pin, or rollback procedure requires it, and all consumers have moved to `OMNIGENT_RUNTIME_HOST_IMAGE_REF`. Documented test `test_host_moonmind_alias_retirement_condition_documented` asserts this.
