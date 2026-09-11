# OpenCode Host and Shared Omnigent Runtime Image

**Status:** Current
**Document Class:** System / Operator Guide  
**Owners:** MoonMind Platform  
**Last updated:** 2026-09-04
**Authority:** OpenCode runtime contract on the shared MoonMind Omnigent host image

## Related documents

- [`docs/Omnigent/README.md`](./README.md) — module entrypoint and contract owners
- [`docs/Omnigent/ContractOwnership.md`](./ContractOwnership.md) — per-file ownership map
- [`docs/Omnigent/SharedHostImage.md`](./SharedHostImage.md) — shared image, runtime packs, Host Classes, credential ownership
- [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)

## Advance organizer

**One sentence:** OpenCode, Codex, and Claude Code use the shared MoonMind Omnigent image with separate Host Classes and credential authority.

**One paragraph:** The image derives from an immutable stock Omnigent host and installs pinned vendor CLIs at build time. Each Host Class, Provider Profile, materializer, and exact-host attestation authorizes only its selected runtime. Credentials remain strictly isolated. A shared image does not share OAuth homes, API keys, materializers, Host Classes, or support evidence.

## 1. Supported runtime

### Current supported implementation

OpenCode currently uses:

```text
opencode-native
+ generic-omnigent-host@1
+ none@1 (OpenCode Zen) or opencode-auth-json@1 (OpenCode Go)
+ omnigent-opencode@1
+ ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:<digest>
```

The image derives from the same immutable Omnigent host base as the stock host and adds the OpenCode CLI at image-build time. No workflow installs OpenCode dynamically.

Conceptually:

```dockerfile
FROM ghcr.io/omnigent-ai/omnigent-host@sha256:<base-digest>
RUN npm install -g --no-audit --no-fund 'opencode-ai@1.18.11'
RUN opencode --version
# Warm the OpenCode plugin SDK closure and prove it resolves offline.
RUN npm install --cache /opt/moonmind/opencode-npm-cache '@opencode-ai/plugin@1.18.11' \
    && npm install --cache /opt/moonmind/opencode-npm-cache --offline '@opencode-ai/plugin@1.18.11'
```

### Shared-image implementation

The shared image, its publication, and its deployment configuration are owned
by [`SharedHostImage.md`](./SharedHostImage.md) §1, which also holds the
verified vendor pins. OpenCode-specific rules that remain here: no workflow
installs OpenCode dynamically, and the warm plugin-SDK cache contract in §4
below. An explicit `OMNIGENT_OPENCODE_HOST_IMAGE_REF` override remains
authoritative for specialized deployments but must pass the same bootstrap
checks; an incompatible pin is quarantined rather than silently replaced.

## 2. Governing image rules

The generic shared-image rules are owned by
[`SharedHostImage.md`](./SharedHostImage.md) §1. The OpenCode-specific rules
that remain here: the image warms every dependency a supported runtime
installs on its own first start (today the OpenCode plugin SDK npm cache) and
proves it resolves offline; a shared image is an implementation artifact, not
a permission boundary — the selected immutable plan still controls which
harness, materializer, Provider Profile, model, and runtime pack are
authorized.

Image admission checks the selected digest's executable Omnigent version and
offline plugin-enabled OpenCode startup from the warm cache. Matching
build labels alone cannot make an image launch-ready. Release CI exercises
plugin-enabled OpenCode startup offline before publishing the shared manifest.

Registration observes the launched container as well as the exact upstream
host identity. An exited host produces `OMNIGENT_HOST_LAUNCH_FAILED` promptly;
only a runnable host may consume the bounded registration wait. The captured
startup log is published before later cleanup operations, so a cleanup outage
does not hide the primary failure. Container cleanup uses typed Docker
inspection and treats an absent container as already removed without probing
unrelated resource types through the Docker proxy. Cleanup and credential
release remain fenced by the durable lease owner.

## 3. Separate Host Classes share the image

OpenCode retains its own Host Class on the shared image. The class inventory
and the one-harness admission rule are owned by
[`SharedHostImage.md`](./SharedHostImage.md) §3; separate Host Classes
preserve independent support and rollout decisions.

A representative OpenCode class remains (`omnigent-opencode@2` is the
shared-image row; `@1` is the dedicated-image legacy row):

```yaml
hostClassId: omnigent-opencode
version: 2
imageRef: ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:...
omnigentBuildDigest: sha256:...
runtimePackRef: opencode-native-pack@1
declaredHarnessImplementations:
  - harnessId: opencode-native
    implementationRef: omnigent-harness-implementation:sha256:...
    runtimeDependencies:
      - name: opencode
        version: 1.18.11
        digest: sha256:...
integrationModes:
  - native-server
materializerRefs:
  - opencode-auth-json@1
  - none@1
features:
  workspaceBind: true
  readOnlyRoot: true
  restrictedEgress: true
  mountedSkills: true
  mountedTools: true
  git: true
  tmux: true
  bubblewrap: true
runtime:
  uid: 1000
  gid: 1000
  home: /home/app
```

The exact shared digest can be promoted for OpenCode without automatically qualifying Codex or Claude.

## 4. OpenCode runtime pack

OpenCode-specific startup and attestation behavior belongs in a trusted versioned runtime pack, not in the generic host lifecycle.

A representative descriptor is:

```yaml
schemaVersion: moonmind.omnigent-harness-runtime-pack.v1
ref: opencode-native-pack@1
harnessId: opencode-native
providerRuntimeId: opencode
binary:
  command: opencode
  version: 1.18.11
  supportedRange: ">=1.17.7,<1.19.0"
credentialMaterializers:
  - opencode-auth-json@1
  - none@1
forbiddenAmbientEnvironment:
  - OPENCODE_API_KEY
  - OPENCODE_AUTH_CONTENT
  - OPENCODE_CONFIG
  - OPENCODE_CONFIG_CONTENT
  - OPENAI_API_KEY
  - ANTHROPIC_API_KEY
hostModes:
  - on-demand
probes:
  version: opencode --version
  models: exact-host OpenCode model catalog helper
```

The runtime pack is deployment-owned. Workflows cannot author its commands, paths, environment, or materializer allowlist.

### OpenCode plugin SDK cache

The first `opencode serve` in a config directory writes
`package.json` = `{"dependencies": {"@opencode-ai/plugin": "<opencode version>"}}`
next to `opencode.json` and runs `npm install` there. Omnigent gives every
session a fresh per-session `XDG_CONFIG_HOME`, and MoonMind gives every
on-demand host a fresh tmpfs home, so a host without a warm cache downloads the
whole closure (about 60 MB) from `registry.npmjs.org` through the restricted
egress proxy while the first user message is blocked on the native-terminal
ensure. That download is not bounded by MoonMind's dispatch budget and was the
cause of the `Omnigent transport error: ReadTimeout` failures replayed by
`tests/integration/reliability/replays/omnigent-opencode-cold-plugin-install/`.

The contract is therefore split across the image and the MoonMind entrypoint:

```text
image build:   npm install --cache /opt/moonmind/opencode-npm-cache  (warm)
               npm install --cache /opt/moonmind/opencode-npm-cache --offline  (prove)
               chown 1000:1000; label moonmind.opencode.plugin_npm_cache
host launch:   test -d /opt/moonmind/opencode-npm-cache || exit 78
               cp -a /opt/moonmind/opencode-npm-cache /home/app/.omnigent/moonmind/opencode-npm-cache
               write /home/app/.npmrc:
                 cache=/home/app/.omnigent/moonmind/opencode-npm-cache
                 prefer-offline=true
                 audit=false
                 fund=false
                 update-notifier=false
release CI:    services/omnigent/opencode-host/verify-warm-plugin-cache.sh <digest>
               (opencode serve becomes ready with --network none)
```

npm reads `$HOME/.npmrc`, and `HOME` is one of the few variables Omnigent
forwards from the host to the OpenCode server, so no `npm_config_*` environment
crosses the host, runner, or server boundary. The seed is copied into the
run-owned state volume rather than the tmpfs home so the cache costs disk, not
the host's memory limit. A host image without the seed fails the launch with an
explicit message in the captured host log instead of degrading to the cold
registry install; deployments that pin `OMNIGENT_OPENCODE_HOST_IMAGE_REF` must
move to a digest built with the warm cache when they adopt this MoonMind
revision.

## 5. Credential materializer: `opencode-auth-json@1`

The OpenCode Go API key remains a run-owned credential materialization.

The trusted boundary writes:

```text
target: /home/app/.local/share/opencode/auth.json
parent mode: 0700
file mode: 0600
owner: 1000:1000
payload: { "opencode-go": { "key": "<secret>", "type": "api" } }
source state: lease-owned Docker volume with generation evidence
host mount: read-only staging attachment
runtime behavior: copy into writable tmpfs home before OpenCode starts
cleanup: remove the run-owned credential volume
```

The materialization sequence is:

1. Acquire the selected Provider Profile lease.
2. Record the acquired credential generation in the fenced runtime binding.
3. Resolve only the `opencode_api_key` SecretRef role at the trusted Activity boundary.
4. Create the labeled run-owned credential volume.
5. Write the exact OpenCode credential shape through a trusted writer container over stdin.
6. Verify the provider key `opencode-go`.
7. Set owner-only modes and runtime ownership.
8. Mount the credential source read-only.
9. Stage it into OpenCode's writable runtime home.
10. Return only a secret-free materialization handle and cleanup authority.
11. Destroy the run-owned material during cleanup without resolving the secret again.
12. Release the Provider Profile lease last.

The runtime script rejects or clears conflicting ambient credentials:

```text
OPENCODE_API_KEY
OPENCODE_AUTH_CONTENT
OPENCODE_CONFIG
OPENCODE_CONFIG_CONTENT
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

The raw key never enters an execution plan, runtime binding, Temporal history, Docker label, ordinary Docker environment variable, workspace, artifact, or cleanup handle.

OpenCode Zen Contributor Free uses `none@1`. It receives no secret role, no
credential attachment, and no API key. The OpenCode runtime wrapper is selected
from the `opencode-native` harness identity, so its startup does not depend on
an `auth.json` mount. A deployment-level `OPENCODE_API_KEY` belongs only to an
explicit `opencode-go` profile and must never be inherited by the Zen profile.

## 6. Shared-image credential isolation

Credential ownership is owned by [`SharedHostImage.md`](./SharedHostImage.md)
§4. The OpenCode-specific isolation proof that remains here — exact-host
conformance must prove, for the selected materializer class:

- `/home/app/.codex` is not mounted from a Codex Provider Profile
- Claude credential paths are not mounted
- no Codex or Claude API-key environment is inherited
- the selected runtime pack is `opencode-native-pack@1`
- the selected materializer is `opencode-auth-json@1` (keyed route) or
  `none@1` (credentialless `opencode-zen-free` route)
- only `opencode-native` is admitted by the selected Host Class
- ambient `OPENCODE_API_KEY`, `OPENCODE_AUTH_CONTENT`, `OPENCODE_CONFIG`,
  `OPENCODE_CONFIG_CONTENT`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` are
  absent from the credentialless `none@1` runtime; `OPENCODE_API_KEY` may only
  configure the separate keyed `opencode-go` profile and never rescues the
  credentialless attempt
- the credentialless route requires no `auth.json` attachment; its startup
  does not depend on an `auth.json` mount

The presence of the other binaries is not a support or authorization signal.

## 7. OpenCode Provider Profile

MoonMind auto-seeds this launch-ready default without a credential:

```yaml
profileId: opencode-zen-free
runtimeId: opencode
providerId: opencode
credentialSource: none
runtimeMaterializationMode: composite
secretRefs: {}
enabled: true
isDefault: true
authState: connected
defaultModel: opencode/muse-spark-1.3-contributor-free
defaultEffort: xhigh
```

The seed runs before the first bootstrap reconciliation. Startup validates the
model against the exact pinned host and publishes the same deployment evidence
required by key-backed profiles. An existing explicit operator disable remains
authoritative.

The seeded `defaultModel`/`defaultEffort` is a configured identity, not
eligibility evidence. It distinguishes the configured seed from the observed
exact-host catalog, the qualified runtime, the eligible pricing/privacy state,
and temporary availability (MoonLadderStudios/MoonMind#4021):

- configured seed: the persisted `opencode-zen-free` profile and its default
  model/effort selection;
- observed catalog: the exact-host model list for the `opencode` route;
- qualified runtime: the pinned image, runtime pack, and `none@1`
  materializer admission;
- eligible pricing/privacy: the `free_model_eligibility` verdict from the
  exact catalog plus trusted pricing/data-use evidence
  (`SELECTION_POLICY_VERSION = free-model-selection.v1`), frozen with the
  admitted attempt;
- temporary availability: provider/host capacity is a wait state that never
  requalifies a model, revokes eligibility, or selects a paid fallback.

No live model availability, pricing, terms, or inference is implied by the
seeded name. When no free candidate is eligible, launches surface
`no_eligible_free_model` with separate availability, pricing, capability, and
privacy reasons through the normal Runtime/Profile UI with a clear reason and
an explicit valid alternative.

The seed declares `isDefault: true`, so it holds runtime-default authority for
`opencode` from first start rather than inheriting it only while it happens to
be the sole launch-ready profile. Two things release that authority, and nothing
else does:

- an explicit operator disable of `opencode-zen-free`, which removes it from the
  launch-ready set and hands the default to the next ranked profile; or
- an explicit default selection on another profile (the Settings
  `make_default` action).

Configuring `OPENCODE_API_KEY` is neither. Deployment enrollment of
`opencode-go-default` validates and enables that profile alongside the Zen
default without transferring runtime-default authority.

The rule is declared on every start, not only when the row is first written.
Startup seeding states `opencode-zen-free` as the runtime-default preference
whenever the profile is not operator-disabled, and
`normalize_runtime_default_profile` settles the single-default invariant from
there. A deployment whose persisted rows still assign the default elsewhere —
including one upgraded from the release where a configured `OPENCODE_API_KEY`
transferred it to `opencode-go-default` — returns the default to the Zen profile
on its next restart.

An explicit default selection is durable because it is persisted, not inferred.
The Settings `make_default` action and an explicit `isDefault` create or update
record `defaultSelectedByOperator` on the chosen profile, and the automatic
preference above never overrules a launchable profile that carries it. The
marker moves with the default: whichever profile loses runtime-default authority
also loses the marker, so at most one profile per runtime carries an operator
claim.

Deployments upgrading to this behavior are cut over once. The migration that
adds `defaultSelectedByOperator` backfills every existing row to `false`,
because releases before it recorded nothing that distinguishes an operator
selection from the automatic transfer. An operator who wants a different
`opencode` profile to hold the default re-selects it once after the upgrade.

Every launch surface derives OpenCode eligibility from the runtime/provider
capability, not from the presence of a secret reference, so a credentialless
profile is advertised and selectable on the same terms as a key-backed one.

Deployment qualification is independent of runtime-default selection. MoonMind
publishes one exact signed entry for each launch-ready OpenCode materializer
class, so an explicitly selected Zen profile retains `none@1` evidence while an
OpenCode Go default retains separate `opencode-auth-json@1` evidence. Changing
the default does not invalidate another launch-ready class or transfer its
credential authority.

An optional OpenCode Go Provider Profile has the following effective shape:

```yaml
profileId: opencode-go-default
runtimeId: opencode
providerId: opencode-go
credentialSource: secret_ref
runtimeMaterializationMode: auth_json
secretRole: opencode_api_key
credentialGeneration: 1
maxParallelRuns: 1
queueWhenBusy: true
enabled: true
authState: connected
defaultModel: opencode-go/muse-spark-1.3-contributor
```

A user may enroll it through Settings with a write-only API-key field. A
deployment may also bootstrap it from `OPENCODE_API_KEY`. Reconciliation repairs
a missing, partially enrolled, or automatically disabled `opencode-go-default`
profile and rotates its stored credential when the configured key changes,
through the same pinned-runtime validation path. A successful rotation commits
the key, incremented credential generation, and catalog evidence together;
failed credential validation preserves the previous credential and evidence.
An unchanged key does not trigger rotation. Existing model, effort, tier policy,
and runtime-default selection remain authoritative, as do explicit user and
policy disables. Other Provider Profiles are not targets of this key rotation.

A nonblank deployment key is authoritative for this default profile; removing
the environment value leaves the enrolled credential intact. After editing
`.env`, recreate the API service with `docker compose up -d api` so its process
loads the changed environment. A container restart alone does not reload
`.env`. Startup reconciliation then applies the new key without requiring a
second update in Settings.

The raw key is never returned after submission.

## 8. Provider validation and model discovery

Validation uses the same pinned runtime and provider-specific materialization
behavior as production:

1. Acquire a temporary Provider Profile maintenance lease.
2. Select `none@1` for `opencode` or materialize the proposed key through `opencode-auth-json@1` for `opencode-go`.
3. Launch the exact digest-pinned OpenCode Host Class on restricted egress.
4. Stage the credential into the writable runtime home only for the key-backed route.
5. Query the exact-host OpenCode model catalog.
6. Require at least one model for the selected OpenCode provider route.
7. Require the selected default model to appear in the observed catalog.
8. Delete the validation host and run-owned credential state.
9. Release the maintenance lease.
10. Persist only normalized model and runtime evidence.

MoonMind does not substitute a configured model when the provider catalog is empty. It does not silently select another model when the prior default disappears.

## 9. Revalidation and freshness

OpenCode model-catalog evidence binds:

- the exact host image digest
- the exact OpenCode runtime version
- the materializer version
- the Provider Profile credential generation
- the selected default model
- the observation time

`OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS` bounds observation age and defaults to `6`. A value of `0` revalidates only when image or credential identity changes.

Refresh is proactive: an observation is re-probed a bounded lead time *before*
it expires, so a healthy deployment never advertises a catalog it has already
declared stale. `OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES` owns that lead.
Omitting it derives the lead from the interval — one tenth of the interval,
capped at five minutes — through the same code path an explicit value takes. An
explicit value is clamped to half the interval, because a lead at or beyond the
interval would make every observation stale the moment it was written.

Discovery needs refresh when:

- the host image changes
- the OpenCode runtime identity changes
- the credential generation changes
- the selected default model is absent
- the observation exceeds the configured age
- the observation is implausibly far in the future

The bootstrap reconciler revalidates connected profiles through their selected
materializer: the existing SecretRef for OpenCode Go and no credential for
OpenCode Zen. It does not request a new key. Discovery freshness is advisory for connected Profiles. A refresh, image change,
or failed background probe never removes a configured Profile from authoring or
revokes previously established credential readiness. The existing durable launch
phase reports preparation progress while the actual host verifies the selected
model and credential materialization. Missing models produce an actionable failure
without substituting a model or account.

Revalidation attempts are bounded per image and credential generation. Failure to acquire the maintenance lease does not spend a provider probe attempt because no probe occurred.

One refresh is single-flight on a **versioned evidence identity**: the probe
contract version, the profile, the pinned host image digest, the credential
generation, the materializer ref, the provider route, and the selected model.
Two maintainers that would produce interchangeable evidence derive the same
identity and collapse onto one probe; anything the launch boundary compares
differently is a different identity that is always allowed to run. The identity
is recorded on the lease itself, so a second request that reuses an owner ID
under a different identity or purpose fails closed rather than inheriting
authority the manager never granted. See
`docs/Security/ProviderProfiles.md` section 11.8.

A probe that completes after the state it assumed has moved on is not
committed. Evidence is written only when it names the image the pass ran
against, that image is still the pinned one, and the profile's credential
generation is unchanged; otherwise the pass defers and the next one re-probes
the current identity.

## 10. Advanced execution configuration

A representative OpenCode Agent Profile remains:

```yaml
profileId: omnigent-opencode-default
version: 1
digest: sha256:...
endpointRef: default
harness:
  id: opencode-native
  catalogRef: omnigent-harness-catalog:sha256:...
  implementationRef: omnigent-harness-implementation:sha256:...
source:
  kind: upstream
  upstreamId: opencode-native-ui
  upstreamVersion: ...
  upstreamSnapshotDigest: sha256:...
credentialSlots:
  - id: primary-model
    acceptedAuthModels:
      - own-auth
      - none
    acceptedProviderIds:
      - opencode
      - opencode-go
hostClassRef: omnigent-opencode@1
runtimePackRef: opencode-native-pack@1
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1
model:
  qualifiedId: opencode-go/<model-id>
allowedLaunchPolicyRefs:
  - omnigent-on-demand@1
```

Users do not author image digests, harness implementation digests, runtime-pack commands, or credential paths.

## 11. Workflow execution

Workflow Create persists the stable top-level identity and immutable profile selection:

```yaml
agentKind: external
agentId: omnigent
executionProfileRef: <OpenCode Go Provider Profile ID>
parameters:
  omnigent:
    agentProfileRef:
      profileId: omnigent-opencode-default
      version: 1
      digest: sha256:...
    launchPolicyRef: omnigent-on-demand@1
    model: opencode-go/<model-id>
```

The trusted planner resolves the runtime pack, Host Class, image, materializer, support key, and `generic-omnigent-host@1` realizer. Temporal and the canonical session lifecycle do not branch on OpenCode as a top-level runtime identity.

## 12. Exact-host attestation

Before runner or session creation, the exact OpenCode host proves:

```text
selected image digest matches the selected Host Class
moonmind.omnigent.build_digest matches the catalog authority
command -v opencode succeeds
opencode --version is within >=1.17.7,<1.19.0
selected runtime pack matches opencode-native
host advertises the exact opencode-native implementation
credential generation is the acquired generation
credential file exists without printing its contents when the selected materializer requires one
credential ownership and modes are correct when credential state is materialized
non-selected runtime credentials are absent
workspace, Skills, and mounted tools match the plan
restricted egress is active
selected model is available to the exact credential
```

The selected-model probe uses Omnigent's portable OpenCode catalog helper inside the exact host when the normal host tunnel does not expose pre-launch OpenCode model options.

The runner-environment, OpenCode shell-environment, and model-catalog probes execute Omnigent's own portable helpers inside the exact host. The runner environment builder lives in `omnigent.host.connect`. The server-environment filter and CLI model catalog use `omnigent.harnesses.opencode_native.app_server` when the image contains the upstream `omnigent.harnesses` package (0.13 layout), or `omnigent.opencode_native_app_server` when that package is absent (0.12 layout). This selection inspects the admitted image, independently of the worker's installed package or the mutable image tag. MoonMind never reimplements those filters or changes the selected image, credential, or model. A missing helper, broken dependency, or signature drift in the selected layout fails with `OMNIGENT_HARNESS_BUILD_MISMATCH` (`align_host_build`); it never triggers a retry with the other layout or becomes a Skill snapshot, credential, or model failure.

## 13. Deployment configuration

The default OpenCode and shared host coordinates use the same repository:

```env
OMNIGENT_OPENCODE_HOST_IMAGE=ghcr.io/moonladderstudios/omnigent-host-moonmind
OMNIGENT_OPENCODE_HOST_IMAGE_TAG=latest
OMNIGENT_SHARED_HOST_IMAGE=ghcr.io/moonladderstudios/omnigent-host-moonmind
OMNIGENT_SHARED_HOST_IMAGE_TAG=latest
```

`OMNIGENT_OPENCODE_HOST_IMAGE_REF` selects an explicit immutable OpenCode host
pin. `OMNIGENT_SHARED_HOST_IMAGE_REF` independently selects the shared host pin.
The OpenCode keys remain the canonical OpenCode configuration; there is no
`OMNIGENT_RUNTIME_HOST_IMAGE*` configuration or alias translation. Omitting the
OpenCode repository in direct API startup selects the same default as Compose.
Mutable image and tag coordinates are resolution inputs only. Launch authority
is always the resolved digest, and explicit pins are never silently replaced.

The deployment resolver treats the current Omnigent server and OpenCode host as
one paired runtime. Before admitting a host, it requires the host's
`moonmind.omnigent.build_digest` label to match the server build identity and
executes `omnigent --version` in both immutable images. It also runs
`services/omnigent/opencode-host/verify-warm-plugin-cache.sh` against the exact
selected OpenCode image, proving plugin-enabled server startup with networking
disabled. Missing or incomplete caches, failed startup, and probe timeouts
block compatibility even when labels and versions match. Host Class selection
and advertised inventory consume that verdict until reconciliation succeeds.

Host admission is keyed by the running server, not by the newest tag. On the
default mutable-tag path the resolver judges the freshly resolved tag first and
the currently admitted host second, and the first candidate that passes the
full compatibility ladder becomes launch authority. When the registry has
already published a host for a newer Omnigent server than Compose is running,
the admitted compatible host stays authoritative and the newer image is
recorded as `opencodeHostCompatibility.pendingHost` (image ref, host build,
host version, and the failure code it would raise). The API logs that pending
pair on every reconciliation pass together with the remediation derived from
its failure code: a server/host build or version drift is adopted by updating
the `omnigent` Compose service, while a host that failed its own qualification
or contradicts `OMNIGENT_BUILD_DIGEST` must be repaired or republished and never
prompts a server update. Once the failure is cured the fresh tag passes and
replaces the admitted host. When server evidence is temporarily unavailable
(for example while the Omnigent container restarts) nothing is judged and the
admitted host is retained as the persisted ref. Quarantine is reserved for the
case where no candidate is compatible with the running server. An explicit
`OMNIGENT_OPENCODE_HOST_IMAGE_REF`
pin is a single candidate: it is quarantined, never replaced. When the shared
host coordinates resolve to the same image the OpenCode path judged, the
shared ref follows the admitted digest so Codex and Claude Host Classes never
launch a host built for a different server.

`OMNIGENT_BUILD_DIGEST` remains optional operator authority only for an independently paired server and host build. An image manifest digest must not be substituted for the separate portable Omnigent build identity.

## 14. Image release and compatibility

The shared release contract requires:

- resolve an immutable Omnigent base image
- build `linux/amd64` and `linux/arm64` variants where supported
- verify `omnigent --version`
- verify `codex --version`
- verify `claude --version`
- verify `opencode --version`
- verify `opencode serve` becomes ready with the network disabled from the warmed plugin SDK npm cache
- verify the expected harness catalog and implementation identities
- record each vendor runtime independently
- generate provenance and SBOM data
- publish a neutral manifest tag and digest

The shared image release workflow runs on a recurring schedule against the
current upstream server and base-host images. Runtime quarantine remains
authoritative during the publication window; successful reconciliation restores
availability only when the selected image passes compatibility and bootstrap
checks.
A new shared image does not automatically replace any Host Class. Each harness promotes the new digest only after its own conformance row passes.

## 15. Qualification and support

OpenCode support remains combination-specific. Qualification binds at least:

```text
MoonMind commit
Omnigent server and shared host build
host image digest and architecture
opencode-native implementation
opencode-native-pack version
OpenCode CLI version
selected credential materializer version (`none@1` or `opencode-auth-json@1`)
Provider Profile compatibility class
Agent Profile version
Host Class and launch policy
selected model and effort
execution realizer
required capabilities
```

A shared-image Codex or Claude pass does not qualify OpenCode. An OpenCode pass does not qualify Codex or Claude.

Protected support classification remains bound to the complete exact support
key, including model, effort, normalized options, and Required Capabilities.
The default local deployment-qualification path instead binds the immutable
runtime substrate and credential compatibility class. Per-run model/options
and Required Capabilities are admitted independently through class admission,
provider/runtime validation, and exact-host model attestation, so selecting a
valid launch-ready Provider Profile does not require changing the runtime
default or manual requalification. The deployment evidence publication retains
one independently signed entry per launchable materializer class; entries are
replaced only by a newly qualified entry for the same deployment-scoped class.

Conformance tiers and live-smoke boundaries are owned by
[`ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md); rollout states
and promotion by [`RuntimeProviderRollout.md`](./RuntimeProviderRollout.md).

## 16. Deployment upgrades and rollback

Operators selecting explicit image pins update the server and host as a
compatible pair. Existing pins remain authoritative until changed; adopting a
new MoonMind revision does not silently switch an old deployment to another
image. The selected image must pass bootstrap admission and exact-host
qualification before new plans can use it.

The OpenCode Host Class retains `omnigent-opencode@1`; its resolved image and
qualification evidence bind the exact immutable digest. Updates and rollbacks
change future selection. They do not rewrite an admitted execution plan or
silently use another digest for an in-flight run.

## 17. Non-goals

This document does not authorize:

- sharing OpenCode credentials with Codex or Claude Code
- one all-powerful Host Class merely because the image contains several CLIs
- treating installed binaries as production support
- workflow-authored runtime packs, image refs, probes, mounts, or Docker options
- package installation during workflow launch
- silent fallback to Codex, Claude, a direct runtime, another model, or another Provider Profile
- rewriting immutable image selections in existing execution plans

## 18. Strategic rule

OpenCode remains the first concrete generic-host proof. It is not the final architecture boundary.

The implementation should be generalized so that the normal differences between OpenCode, Codex, and Claude Code are limited to:

- runtime-pack registration
- credential materialization
- exact runtime and authentication probes
- truthful capability normalization
- combination-specific support evidence

Everything else should converge on the shared generic Omnigent host and control plane.
