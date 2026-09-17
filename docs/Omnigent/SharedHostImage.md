# Shared Omnigent Host Image and Runtime Packs

**Status:** Shared image, runtime packs, Codex/Claude Host Classes, OAuth-home materializers, and rollout mechanisms implemented; exact qualification, deployment promotion, and retirement remain evidence-gated
**Document Class:** System / Operator Guide
**Owners:** MoonMind Platform
**Last updated:** 2026-09-06
**Authority:** Shared host image contract and runtime-pack descriptor authority for the Omnigent primary-runtime program

## Related documents

- [`docs/Omnigent/README.md`](./README.md) — module entrypoint and contract owners
- [`docs/Omnigent/ContractOwnership.md`](./ContractOwnership.md) — per-file ownership map
- [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md)
- [`docs/Omnigent/RuntimeProviderRollout.md`](./RuntimeProviderRollout.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)
- [`docs/Omnigent/OpenCodeHost.md`](./OpenCodeHost.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/MoonMindRoadmap.md`](../MoonMindRoadmap.md)

## Server compatibility and deployment readiness

Omnigent server/host interoperability uses equal major.minor release series;
patch versions and build digests may differ. This rule also applies to `0.x`
releases. Required capabilities, credential boundaries, runtime-pack checks,
and the exact selected host image remain enforced. Server digests record
observed deployment provenance; host build labels record the selected host's
provenance independently. Bootstrap records build and executable-version
observations keyed by each selected immutable host image, including independently
built shared and Pi images. Host selection consumes that image's observation;
missing evidence cannot borrow the catalog/server digest. `OMNIGENT_BUILD_DIGEST`
is an optional exact host-build pin. It is never a substitute for the server
image digest, and publication does not overwrite it with a discovered digest.

Execution plans obtain version evidence from the immutable `harnessCatalogRef`
already in the v1 contract. New plans omit the redundant top-level
`omnigentVersion`, even when null, so retained readers do not need to match the
writer's MoonMind release. A compatible server patch update
can serve the unchanged plan and its original immutable host image. Rehydration
verifies the recorded launch artifact and uses the admitted host image, build,
architecture, and runtime settings even when current default-host discovery is
missing or has moved to a different release series. Partial recorded host
authority and mismatched launch artifacts remain rejected. Persisted plans
that contain inline version evidence preserve that field and their canonical
bytes. Catalog evidence is read by its admitted ref and checked against the
recorded endpoint and build; missing evidence is retryable and a conflicting
catalog cannot authorize launch. An unknown version is never guessed. Core catalog
refreshes may attest a patch update only when the declared harness contract is
unchanged. Plugin implementation identities remain exact.

Compose infrastructure discovery starts after the Omnigent server container.
Incomplete discovery is reported as pending readiness. The API bootstrap
reconciler owns re-observation independently of the agent execution queue.
Fresh allocations validate deployment readiness before claiming a canonical turn
command or acquiring credentials, so a discovery gap leaves delivery unclaimed.
An admitted one-Activity execution encountering `OmnigentDeploymentNotReady`
waits durably in `MoonMind.AgentRun`, retaining its request, admission, workspace,
profile, and cumulative execution budget. It retries every 30 seconds for at
most the 900-second handoff allowance or remaining execution budget, whichever
is smaller. Cancellation remains available. Exhaustion records a readiness
timeout without another admission or a fresh execution budget. Terminal
saved-work/finalization evidence remains authoritative during discovery gaps.

## Advance organizer

**One sentence:** One neutral digest-pinned host image carries every approved vendor runtime, while trusted runtime-pack descriptors, separate Host Classes, profile-owned credential materialization, and a gated planner keep harness support, credentials, and lifecycle authority exact.

**One paragraph:** `ghcr.io/moonladderstudios/omnigent-host-moonmind` is the one host image Codex, Claude Code, and OpenCode Host Classes share. A runtime pack (`codex-native-pack@1`, `claude-native-pack@1`, `opencode-native-pack@1`) is the deployment-owned descriptor that declares one vendor runtime's pinned version, supported range, credential-home layout, bounded environment, and readiness probes. `omnigent-codex@1` and `omnigent-claude@1` are separate Host Class templates on the same digest: same image, no shared harness support. `codex-oauth-home@1` and `claude-oauth-home@1` attach the enrollment-owned OAuth credential home with profile-owned semantics (read-write, fenced by generation, detached but preserved on cleanup). The trusted planner routes `codex-native` to `codex-profile-bound@1` until the generic Codex combination is qualified, and `claude-native` to `generic-omnigent-host@1`. Explicit generic selection before qualification fails closed and never falls back.

The [seven product outcomes](./PrimaryRuntimeProviderStrategy.md#11-seven-required-product-outcomes) are the acceptance contract for this infrastructure. A shared image is not the product outcome by itself: each claimed harness must complete the on-demand launch, bound Workflow Detail interaction, recovery, durable-evidence, and cleanup journey. Runtime and one Profile remain the ordinary choices; the image, Host Class, runtime pack, materializer, and execution configuration resolve behind that selection.

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

The publish workflow tracks `omnigent-server:latest`, so a republished tag may
carry a host built for a newer server than a deployment is running. When the
shared coordinates resolve to the same image the OpenCode path judged against
the running server, the shared ref follows the admitted OpenCode digest and the
newer image waits as `pendingHost` until the operator updates the `omnigent`
Compose service (see [`OpenCodeHost.md`](./OpenCodeHost.md) §13). An explicit
`OMNIGENT_SHARED_HOST_IMAGE_REF` pin is never replaced.

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
omnigent-codex@1     codex-native     + codex-native-pack@1     + codex-oauth-home@1, none@1
omnigent-claude@1    claude-native    + claude-native-pack@1    + claude-oauth-home@1, none@1
omnigent-opencode@2  opencode-native  + opencode-native-pack@1  + opencode-auth-json@1, none@1
```

(`omnigent-opencode@1` remains the dedicated-image legacy row for historical
reads; `@2` is the shared-image row.)

Separate classes reference the same `OMNIGENT_SHARED_HOST_IMAGE_REF` digest without conflating harness support. Each class declares only its own harness, runtime pack, and materializers, so a shared image never authorizes every installed runtime. One-harness admission is enforced at selection (the pack must own the harness, the materializers must be allowlisted) and again at planning (the Host Class must declare the plan's harness implementation).

## 4. Credential ownership

| Materializer | Ownership | Behavior |
| --- | --- | --- |
| `opencode-auth-json@1` | run | Keyed OpenCode Go route: per-run volume, destroyed on cleanup |
| `codex-oauth-home@1` | profile | Enrollment-owned OAuth home volume attached read-write; generation-marker fenced; detached but preserved on cleanup |
| `claude-oauth-home@1` | profile | Same profile-owned contract for the Claude enrollment home |
| `none@1` | none | Credentialless OpenCode Zen creates no auth state or dummy secret; its real Provider Profile still governs routing and capacity |
| `host-owned-auth@1` | host | No runtime state copied or deleted; only supported static-host combinations may use this authority |

Profile-owned materializers resolve no secret: the enrollment-owned volume populated by MoonMind Settings OAuth enrollment *is* the credential state. Materialization attaches it, stages an idempotent `.moonmind-generation` marker (which rejects a newer, rotated generation so a stale lease can never fence a replacement home), and writes secret-free evidence. Cleanup is detach-only: it verifies the fence, preserves the durable enrollment volume, and never deletes credential state. Raw credential contents never appear in plans, handles, bindings, Docker metadata, Temporal history, artifacts, or logs.

The legacy static deployment's single shared `codex_auth_volume` / `claude_auth_volume` is the enrollment state these materializers attach; it is unchanged by this design.

An existing Codex or Claude Code OAuth Provider Profile remains the account and capacity authority when its execution uses Omnigent. The subordinate Agent Profile does not create a second account or login ceremony. Direct and Omnigent consumers must coordinate through the same credential-generation lease authority. The shared image does not authorize concurrent writable use of an OAuth home, and the credentialless OpenCode route never inherits the keyed route's credentials.

## 5. Realizer admission

The trusted planner (never the workflow) selects the execution realizer:

- `codex-native` keeps `codex-profile-bound@1` until the operator sets `MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED=true` after exact shared-image Codex evidence passes. Before then, an explicit `generic-omnigent-host@1` Codex selection fails closed.
- `claude-native` requires `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED=true`; before then, planning a `claude-native` combination fails closed rather than advertising an unqualified target.
- Those operator flags are inputs to the versioned runtime-provider rollout policy, which is the one authority that decides whether a combination is a default, an explicit-only choice, a labeled compatibility path, or unavailable. See [`docs/Omnigent/RuntimeProviderRollout.md`](./RuntimeProviderRollout.md).

Both gates default to false. No generic plan silently falls back to a direct, legacy, or another-harness path: a failed generic launch returns a typed terminal failure through the same plan and fenced binding.

## 6. Implementation mechanisms and evidence-gated outcomes

Infrastructure implementation, support qualification, product-default promotion, and removal are separate milestones owned by the existing primary-runtime program. A completed mechanism must not be listed as unimplemented, and its completion must not be used as evidence that the corresponding user journey or retirement has passed.

- **Exact support evidence** (#3832): the versioned required-row catalog and deterministic validators live in `moonmind/omnigent/harness_platform/shared_host_conformance.py` (`moonmind.omnigent-shared-host-rows.v1`, covered by `tests/unit/omnigent/test_shared_host_conformance.py`). The catalog distinguishes Codex OAuth, Claude OAuth, keyed OpenCode, and credentialless OpenCode. Its required rows carry pending protected-live evidence until the provider-verification runner qualifies the exact combination. Installed binaries and pure validators do not qualify a deployed digest or prove credential isolation on a real host.
- **Product-default migration** (#3833): the versioned rollout policy, shared selection/admission boundary, canary/rollback controls, and migration telemetry exist. Promotion remains specific to a qualified deployment combination and must preserve the selected Profile and configuration across every authoring and follow-up surface. Do not rebuild those mechanisms or add an independent Target selector in order to complete promotion.
- **Compose convergence** (#3834): shared image and startup configuration must be evaluated separately from qualification of an actual static host. Static-connected support is optional and separately evidenced; it must not become a prerequisite for ordinary on-demand execution. Removal of obsolete startup paths follows the existing retirement owner.
- **Static Claude disposition** (#3936): the `omnigent-host-claude` Compose profile is **retained** for existing deployments; the on-demand generic Claude host is the primary destination. The disposition is recorded in code as `moonmind.omnigent.harness_platform.static_hosts.static_claude_disposition` and matches retirement row `omnigent.legacy.claude_static_host_startup` (still `active_product_path`, admitting through `docker-compose.yaml:omnigent-host-claude`). Only a digest-pinned `OMNIGENT_SHARED_HOST_IMAGE_REF` (or the bounded `OMNIGENT_HOST_IMAGE_REF` alias when the shared ref is unset) is supported launch authority; the `OMNIGENT_SHARED_HOST_IMAGE` / `OMNIGENT_SHARED_HOST_IMAGE_TAG` fallback is a development/bootstrap input resolved to a digest by `moonmind.omnigent.bootstrap.image_resolution` before admission (`resolve_effective_static_host_image` fails closed otherwise). Static-host readiness waits are bounded (`MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS`, `MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS`): expiry reports waiting-for-enrollment / missing-projection, never admitted capacity. Hermetic qualification lives in `tests/unit/omnigent/test_static_claude_qualification_3936.py`; the Profile -> static-host binding/attestation -> canonical session/turn -> terminal evidence -> cleanup journey still requires exact-image Docker runs and authorized live-provider evidence. Removal follows the #3835 row at the `STARTUP_AND_COMPOSE` stage after the exact-image and lifecycle gates pass.
- **Retirement** (#3835): the code-owned inventory in `moonmind/omnigent/legacy_retirement.py` classifies retained direct, profile-bound, startup, Compose, configuration, replay, and historical-read components, enforces class-based new-admission control, and gates staged removal behind drain, replay, historical-read, rollback, and retention evidence. An inventory or guard is not a completed removal. The `omnigent-host-opencode` alias and `OMNIGENT_OPENCODE_HOST_IMAGE_REF` remain active with a retirement row; they are removed at the image and environment alias stage once no dependency remains. See [Omnigent Module Architecture §5](OmnigentModuleArchitecture.md#5-retained-duplicate-architecture-and-its-retirement-owners).
