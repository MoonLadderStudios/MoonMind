# Shared Omnigent Host Image and Runtime Packs

**Status:** Shared image, runtime packs, Codex/Claude Host Classes, and OAuth-home materializers implemented; exact support evidence, default rollout, and retirement remain per-child-issue work
**Document Class:** System / Operator Guide
**Owners:** MoonMind Platform
**Last updated:** 2026-09-04
**Authority:** Shared host image contract and runtime-pack descriptor authority for the Omnigent primary-runtime program

## Related documents

- [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)
- [`docs/Omnigent/OpenCodeHost.md`](./OpenCodeHost.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/MoonMindRoadmap.md`](../MoonMindRoadmap.md)

## Advance organizer

**One sentence:** One neutral digest-pinned host image carries every approved vendor runtime, while trusted runtime-pack descriptors, separate Host Classes, profile-owned credential materialization, and a gated planner keep harness support, credentials, and lifecycle authority exact.

**One paragraph:** `ghcr.io/moonladderstudios/omnigent-host-moonmind` is the one host image Codex, Claude Code, and OpenCode Host Classes share. A runtime pack (`codex-native-pack@1`, `claude-native-pack@1`, `opencode-native-pack@1`) is the deployment-owned descriptor that declares one vendor runtime's pinned version, supported range, credential-home layout, bounded environment, and readiness probes. `omnigent-codex@1` and `omnigent-claude@1` are separate Host Class templates on the same digest: same image, no shared harness support. `codex-oauth-home@1` and `claude-oauth-home@1` attach the enrollment-owned OAuth credential home with profile-owned semantics (read-write, fenced by generation, detached but preserved on cleanup). The trusted planner routes `codex-native` to `codex-profile-bound@1` until the generic Codex combination is qualified, and `claude-native` to `generic-omnigent-host@1`. Explicit generic selection before qualification fails closed and never falls back.

## 1. Shared host image

### Identity

```text
ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest>
```

The image derives from the digest-pinned stock Omnigent host base and installs at build time only:

```text
@openai/codex@0.104.0
@anthropic-ai/claude-code@2.1.257
opencode-ai@1.18.11
```

Workflow launches never install runtimes. Every installed vendor version must sit inside its runtime-pack supported range (inclusive lower, exclusive upper); a drifted runtime fails the image build instead of becoming launch authority.

### Publication

`.github/workflows/docker-publish-moonmind-host.yml` builds and publishes the multi-arch (`linux/amd64`, `linux/arm64`) image to GHCR with provenance and SBOM. It is the same release pattern as the `omnigent-host-opencode` workflow: resolve the digest-pinned base, build by digest per platform, verify every vendor runtime and the warm OpenCode plugin npm cache inside the image, merge the manifest list, and print the digest-pinned deployment ref.

### Deployment configuration

```text
OMNIGENT_SHARED_HOST_IMAGE_REF=""        # digest-pinned ref; leave empty to resolve
OMNIGENT_SHARED_HOST_IMAGE="ghcr.io/moonladderstudios/omnigent-host-moonmind"
OMNIGENT_SHARED_HOST_IMAGE_TAG="1.18.11"
```

Mutable tags never become launch authority. Startup resolution (`moonmind/omnigent/bootstrap/image_resolution.py`) resolves the tag to its immutable digest once, persists it in the resolved deployment state (`sharedHostImageRef`), exports it to `OMNIGENT_SHARED_HOST_IMAGE_REF`, and every selector (`get_shared_host_image_ref()`) reads that digest. A missing, mutable, or placeholder digest fails closed.

## 2. Runtime packs (`moonmind.omnigent-harness-runtime-pack.v1`)

A runtime pack is trusted deployment data, never workflow-authored. The registry is pure data: it carries no secret, image digest, or endpoint. One pack owns one harness family:

| Pack | Harness | Vendor runtime | Credential home |
| --- | --- | --- | --- |
| `codex-native-pack@1` | `codex-native` | `codex >=0.100.0,<0.200.0` (pin `0.104.0`) | `/home/app/.codex` |
| `claude-native-pack@1` | `claude-native` | `claude >=2.0.0,<3.0.0` (pin `2.1.257`) | `/home/app/.claude` |
| `opencode-native-pack@1` | `opencode-native` | `opencode >=1.17.7,<1.19.0` (pin `1.18.11`) | `/home/app/.local/share/opencode` |

Each pack declares the vendor version command, the supported range, the credential-home layout (target path, writability, uid/gid ownership), the bounded environment the generic startup may shape, the ambient environment keys the row must reject, and the exact-host readiness probe kind.

The packs drive two production boundaries:

1. **Host Class selection.** Pack-backed Host Class templates carry `runtime_pack_ref`; the selector resolves the pack, proves it owns the requested harness, and compiles the vendor runtime dependencies from the pack. A pack/harness mismatch or an unknown pack fails selection.
2. **Exact-host attestation.** `validate_runtime_pack_preflight` validates a launched container against its pack: the pack must own the attested harness, the attested vendor runtime version must sit inside the pack range, and the declared readiness and restricted-egress capabilities must be positively reported. The OpenCode preflight is this same descriptor-driven function with `opencode-native-pack@1`; there are no harness-specific attestation branches.

Changing a vendor pin is a deployment change that must land in the image build args and the pack descriptors in the same change; exact-host attestation rejects a drifted runtime.

## 3. Host Classes on the shared digest

```text
omnigent-codex@1    codex-native   + codex-native-pack@1   + codex-oauth-home@1, none@1
omnigent-claude@1   claude-native  + claude-native-pack@1  + claude-oauth-home@1, none@1
```

Separate classes reference the same `OMNIGENT_SHARED_HOST_IMAGE_REF` digest without conflating harness support. Each class declares only its own harness, runtime pack, and materializers, so a shared image never authorizes every installed runtime. One-harness admission is enforced at selection (the pack must own the harness, the materializers must be allowlisted) and again at planning (the Host Class must declare the plan's harness implementation).

## 4. Credential ownership

| Materializer | Ownership | Behavior |
| --- | --- | --- |
| `opencode-auth-json@1` | run | Per-run volume, destroyed on cleanup |
| `codex-oauth-home@1` | profile | Enrollment-owned OAuth home volume attached read-write; generation-marker fenced; detached but preserved on cleanup |
| `claude-oauth-home@1` | profile | Same profile-owned contract for the Claude enrollment home |
| `none@1` / `host-owned-auth@1` | none / host | No runtime state copied or deleted |

Profile-owned materializers resolve no secret: the enrollment-owned volume populated by MoonMind Settings OAuth enrollment *is* the credential state. Materialization attaches it, stages an idempotent `.moonmind-generation` marker (which rejects a newer, rotated generation so a stale lease can never fence a replacement home), and writes secret-free evidence. Cleanup is detach-only: it verifies the fence, preserves the durable enrollment volume, and never deletes credential state. Raw credential contents never appear in plans, handles, bindings, Docker metadata, Temporal history, artifacts, or logs.

The legacy static deployment's single shared `codex_auth_volume` / `claude_auth_volume` is the enrollment state these materializers attach; it is unchanged by this design.

## 5. Realizer admission

The trusted planner (never the workflow) selects the execution realizer:

- `codex-native` keeps `codex-profile-bound@1` until the operator sets `MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED=true` after exact shared-image Codex evidence passes. Before then, an explicit `generic-omnigent-host@1` Codex selection fails closed.
- `claude-native` owns `generic-omnigent-host@1` directly; `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED` gates advertisement for follow-up rollout surfaces.

Both gates default to false. No generic plan silently falls back to a direct, legacy, or another-harness path: a failed generic launch returns a typed terminal failure through the same plan and fenced binding.

## 6. Explicitly deferred

These remain the owned child issues of the primary-runtime program and are not claimed by this design:

- **Exact support evidence** (#3832): the support-key matrix, protected-live conformance rows for generic Codex/Claude, and qualification of the shared digest.
- **Product-default migration** (#3833): versioned rollout policy, per-surface default promotion, canary/rollback controls, and migration telemetry.
- **Compose consolidation** (#3834): converging static Codex/Claude hosts and startup scripts onto the shared image and generic startup.
- **Retirement** (#3835): the code-owned retirement inventory and gated removal of direct and profile-bound lanes.
