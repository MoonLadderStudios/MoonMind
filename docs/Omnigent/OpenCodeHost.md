# OpenCode Host (omnigent-host-opencode)

**Status:** Implemented; launch remains qualification-gated
**Document Class:** System / Operator Guide  
**Owners:** MoonMind Platform  

## Summary

`omnigent-host-opencode` is the first explicit harness-specific Omnigent host realization. It derives from the same immutable Omnigent host base as the stock host, then adds only the OpenCode CLI at **build time**. No per-workflow `npm install` is required.

```
ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest>
FROM ghcr.io/omnigent-ai/omnigent-host@sha256:<base-digest>
RUN npm install -g --no-audit --no-fund 'opencode-ai@1.18.11' && opencode --version
```

## Compatibility

- Omnigent host base: same revision as `OMNIGENT_HOST_IMAGE_REF` (digest-pinned)
- OpenCode: `1.18.11` (supported range `>=1.17.7,<1.19.0`)
- Build fails when `opencode --version` is outside the supported range
- Architecture: `linux/amd64`, `linux/arm64` (multi-arch manifest)

## Host Class

```
hostClassId: omnigent-opencode
version: 1
ref: omnigent-opencode@1
imageRef: ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:...
omnigentBuildDigest: sha256:...
declaredHarnessImplementations:
  - harnessId: opencode-native
    implementationRef: omnigent-harness-implementation:sha256:...
    runtimeDependencies:
      - name: opencode
        version: 1.18.11
        digest: sha256:...
integrationModes: [native-server]
materializerRefs: [opencode-auth-json@1]
features:
  workspaceBind, readOnlyRoot, restrictedEgress, mountedSkills, mountedTools, git, tmux, bubblewrap
runtime: {uid: 1000, gid: 1000, home: /home/app}
```

This class declares **only** `opencode-native`. Codex continues through `codex-profile-bound@1` on its existing `omnigent-codex-current@1` / `omnigent-native-standard@3` classes. No workflow silently switches between them.

## Credential materializer: opencode-auth-json@1

Trusted boundary writes:

```
target: /home/app/.local/share/opencode/auth.json  (0600, uid 1000:1000, parent 0700)
payload: { "opencode-go": { "key": "<secret>", "type": "api" } }
providerKey: opencode-go (verified against pinned CLI)
state: lease-owned Docker volume with generation sidecar
mount: read-only
cleanup: remove the run-owned volume without resolving the secret again
```

Steps (issue §5):

1. Acquire Provider Profile lease
2. Record acquired generation in fenced runtime binding (sticky)
3. Resolve only the `opencode_api_key` SecretRef role at the trusted Activity boundary
4. Create a labeled lease-owned Docker volume
5. Send the exact OpenCode credential structure to a trusted writer container over stdin
6. Verify provider key `opencode-go`
7. Write to `/home/app/.local/share/opencode/auth.json`
8. Parent `0700`, file `0600`
9. Ownership `1000:1000`
10. Mount read-only
11. Return secret-free handle + cleanup authority
12. Destroy on cleanup

Forbidden ambient credentials are rejected:

```
OPENCODE_AUTH_CONTENT, OPENCODE_CONFIG, OPENCODE_CONFIG_CONTENT,
OPENAI_API_KEY, ANTHROPIC_API_KEY
```

Never use `OPENCODE_AUTH_CONTENT`, CLI arguments, labels, generated environment files, or ordinary Docker environment variables for the production secret path. The execution plan, runtime binding, Temporal payloads, Docker inspection data, and cleanup handles contain only references and generations.

## Deployment setup

The canonical local path needs one value. Set `OPENCODE_API_KEY` in `.env` and
start Docker Compose; the Omnigent bootstrap reconciliation does the rest on
startup and on its ongoing cadence:

1. resolve the configured OpenCode host image and tag to an immutable digest,
   read its required `moonmind.omnigent.build_digest` label as the shared
   server/host build identity, and export both authorities for Host Class
   selection and launch policy compilation
   (`OMNIGENT_OPENCODE_HOST_IMAGE_REF` stays optional and overrides image
   resolution when set; the image manifest digest is not substituted for the
   distinct build identity);
2. synchronize the authenticated harness catalog and seed the built-in
   `omnigent-opencode-default` Agent Profile;
3. enroll the `opencode-go-default` Provider Profile from `OPENCODE_API_KEY`,
   running the same pinned-runtime validation as the console action; and
4. keep that profile's model catalog evidence current as the host image is
   re-resolved.

`OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE` defaults to `true` because the stock
OpenCode Go model is a contributor tier whose enrollment records a data-use
acknowledgement. Set it to `false` to decline; enrollment then stops with an
actionable failure instead of acknowledging on the operator's behalf.

Enrollment runs the full bootstrap, so it waits for the immutable image and
harness catalog authorities it depends on, and it is attempted once per distinct
configuration. Correcting the credential or the acknowledgement produces a new
configuration and retries; so does restarting the service. Re-validating an
already-enrolled credential is separate and repeats on every pass.

Console enrollment through **Settings → Provider Profiles** remains available and
is the path for a deployment that does not carry the credential in its
environment.

## Provider Profile: OpenCode Go

Create in **Settings → Provider Profiles**:

- **Profile ID**: e.g. `opencode-go-default`
- **Runtime**: `opencode`
- **Provider**: `opencode-go`
- **Credential source**: `secret_ref`
- **Secret role**: `opencode_api_key` (`db://<slug>`)
- **Enabled**: `true` only when `auth_state=connected`
- **Default qualified model**: e.g. `opencode-go/gpt-5` or any `opencode-go/<model-id>` from discovery
- **Max parallel runs**: `1` by default
- **Queue when busy**: `true`
- **Tags**: optional

Validation (Settings → Validate):

1. Acquire a temporary Provider Profile maintenance lease
2. Materialize the proposed key through `opencode-auth-json@1` into a disposable Docker volume
3. Launch the digest-pinned OpenCode host image on restricted egress, stage the read-only credential into OpenCode's writable data directory exactly as the production host does, and query the refreshed CLI model catalog
4. Require at least one `opencode-go/<model-id>` result
5. Delete the validation host and credential volume, then release the maintenance lease
6. Persist only normalized model metadata, image/runtime identity, and validation evidence

An empty provider catalog fails validation; MoonMind never substitutes a
configured or legacy model as evidence. The selected default must also remain
present in the observed catalog. If the provider removes it, readiness is
deferred until an operator selects another observed model, because changing a
billing-relevant model is not an automatic recovery action. Raw key is never
returned after submission.

### Automatic re-validation after a host image change or catalog interval

Model catalog evidence answers two separate questions, and both must hold for a
persisted observation to stay authoritative.

The first is **launchable identity**: evidence is bound to the exact
digest-pinned host image that produced it, to the credential generation that
was active at the time, to the `opencode-auth-json@1` materializer, and it must
contain the profile's selected default model.

The second is **observation age**. The pinned host image ships a bundled model
catalog and refreshes it from the provider at probe time, so identity alone
would freeze whichever catalog the first successful probe observed: a healthy
deployment never changes its credential generation or image digest on its own,
and models the provider publishes later — contributor tiers included — would
never reach selection, readiness, or admission.
`OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS` bounds that age and defaults to `6`, so
the documented one-value setup keeps observing the provider's current models
without another operator action. Set it to `0` to re-probe only on an identity
change. A pass that has just observed the catalog is judged on identity alone;
the interval decides when to re-probe, not whether a fresh observation counts.

Readiness, planning, and smoke admission all apply both questions: they reject
evidence that belongs to any other image or generation, and they reject an
observation older than the interval. Re-pulling or rebuilding the OpenCode host
image therefore invalidates every previously validated Provider Profile, and an
expired catalog stops advertising and launching targets until a refresh
succeeds instead of admitting a model the provider may have removed.

An observation stamped further than a small clock-skew tolerance into the
future is rejected the same way. A VM snapshot restore or a backward host-clock
correction would otherwise give the observation a negative age that satisfies
any interval for as long as the timestamp stays ahead, keeping an old catalog
authoritative well past its expiry.

MoonMind therefore re-validates automatically. The Omnigent bootstrap
reconciliation pass re-runs the pinned-runtime validation above for every
enabled, connected OpenCode Provider Profile whose evidence no longer matches
the currently pinned image or has aged past the catalog interval, using the
already-enrolled `opencode_api_key` SecretRef. No new credential is requested,
no image is substituted, and a credential the runtime rejects keeps its previous
evidence and stays connected.
Fresh catalog evidence is retained when the credential remains valid but the
configured default model has rotated out, while readiness stays deferred rather
than silently selecting a replacement.
While a profile is waiting for that pass, Workflow Create reports
`provider_runtime_revalidation_pending` rather than asking the operator to
reconnect a profile that is already connected.

That wait is bounded. Re-validation gets `MAX_REVALIDATION_ATTEMPTS` tries per
credential generation and host image; once they are spent, the reconciler stops
probing the provider for that identity and readiness stops reporting a pending
wait. This is what keeps a provider outage or a revoked key from launching a
Docker-backed probe on every background pass for as long as the deployment
runs. Reconnecting the credential or re-pinning the host image is a new
identity, so it restores the full attempt budget without a separate reset
action.

## Agent Profile

```
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
    acceptedAuthModels: [own-auth]
    acceptedProviderIds: [opencode, opencode-go]
hostClassRef: omnigent-opencode@1
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1
model:
  qualifiedId: opencode-go/<model-id>
allowedLaunchPolicyRefs: [omnigent-on-demand@1]
```

The guided editor asks for the preset, model, host policy, workspace mutation, Skills, tools, capture, continuation, and publication behavior. The server resolves the current upstream projection plus exact catalog and implementation authority; users do not author implementation digests.

Workflow Create persists the immutable profile selection and ordinary product intent:

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

Planning resolves the Host Class, credential materializer, image, acquired credential generation, and `generic-omnigent-host@1` realizer from durable data. Temporal dispatch does not branch on harness identity.

## Deployment qualification and execution evidence

Deployment qualification proves this deployment can run one exact harness,
host, image, realizer, credential, and model combination. Admission matches a
compiled plan against that qualification and refuses anything outside it.

Two rules keep qualification and admission on one combination:

- **Qualification never restates plan inputs.** The launch policy is part of
  the qualified combination, so qualification derives it from the Agent Profile
  with the same rule admission uses — the first entry of
  `allowedLaunchPolicyRefs` when the workflow authors none. A restated ref
  attests a combination no plan ever compiles: readiness still reports `ready`
  while every launch fails with `execution evidence unavailable`.
- **Per-run request intent is not a qualification dimension.**
  `requiredCapabilitiesDigest` records what one workflow asked for, not what the
  deployment can run, so it is excluded from the qualified combination. Class
  admission derives support only from trusted catalog, Host Class, launch-policy,
  materializer, bridge, and deployment-mounted-tool declarations; a workflow
  cannot declare its own request supported. Admission independently refuses
  unsupported or unknown required capabilities before the support key exists.
  Binding evidence to one capability set would require re-qualifying the
  deployment for every workflow shape.

Everything else stays exact. Changing an Agent Profile, host image, harness
catalog, model, or effort changes the qualified combination and invalidates the
published evidence. The startup and maintenance reconciler detects drift in the
deployment-managed Agent Profile, Provider Profile defaults, resolved image, or
signed evidence and automatically re-runs the production qualification from the
persisted Provider Profile. The normal `OPENCODE_API_KEY` path therefore stays
launch-ready across service and upstream-agent refreshes without another
operator action or another copy of the credential.

The retry endpoint remains an explicit recovery control when an earlier
qualification attempt failed:

```
POST /api/omnigent/bootstrap/opencode/retry
```

Retry checks that the persisted Provider Profile and its managed SecretRefs are
launch ready, revalidates its credential evidence against the pinned runtime,
and refreshes the desired model and effort from the current Provider Profile.
Qualification uses the current Agent Profile's default launch policy. An
explicit per-run override outside those defaults remains a separate
qualification target; the automatic reconciler owns only the standard managed
default combination.

## Exact-host attestation (issue §8)

Before runner/session creation, the exact host is verified:

```
command -v opencode
opencode --version          # within >=1.17.7,<1.19.0
host advertises opencode-native
implementation identity matches plan
image digest matches Host Class
Omnigent build matches
credential file exists at /home/app/.local/share/opencode/auth.json without printing contents
ownership 1000:1000 and modes 0700/0600
acquired generation is the one materialized
Skill delivery and mounted tools match plan
enforced network/egress policy active
selected model available to credential
```

The selected-model check runs Omnigent's portable
`list_opencode_cli_model_options` helper inside the exact digest-pinned host,
after the fenced credential generation has been materialized and the restricted
egress attachment has been verified. The stock Omnigent host tunnel does not
currently expose pre-launch model options for `opencode-native`; using the
upstream helper directly keeps the attestation live and credential-bound without
duplicating OpenCode catalog semantics in MoonMind. Other harnesses continue to
use the normal host `model-options` tunnel. Readiness by harness name alone is
insufficient.

## Deployment configuration

```
MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED=true
MOONMIND_OMNIGENT_OPENCODE_ENABLED=true
OMNIGENT_OPENCODE_HOST_IMAGE_REF=ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest>
# Optional mutable build/pull coordinates; never launch authority:
OMNIGENT_OPENCODE_HOST_IMAGE=ghcr.io/moonladderstudios/omnigent-host-opencode
OMNIGENT_OPENCODE_HOST_IMAGE_TAG=1.18.11
# Optional only for an independently built, explicitly paired server/host set:
OMNIGENT_BUILD_DIGEST=sha256:<shared-server-and-host-build-identity>
```

- Keep `MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED=false` until the generic services, database migration, Docker backend, endpoint, images, and egress policy are configured.
- Keep `MOONMIND_OMNIGENT_OPENCODE_ENABLED=false` until the protected OpenCode Go conformance workflow has published evidence for the exact support combination.
- Build from pinned Omnigent source/base image
- Publish to GHCR with provenance and digest evidence
- Make the immutable digest available to the data-driven Host Class selector
- Resolve mutable build coordinates on each reconciliation pass, but launch
  only the resulting digest-pinned image after its declared build identity and
  server/host version pairing have been verified
- Image is pulled lazily only when an OpenCode workflow requires it; cached layers are reused

Synchronize the authenticated Omnigent endpoint after deployment or plugin changes:

```text
POST /api/omnigent/harness-catalog/synchronize
```

Synchronization reads `/v1/harnesses`, `/v1/agents`, and `/v1/hosts`, normalizes one immutable build-bound catalog snapshot, and persists exact-implementation trust records plus plugin-load diagnostics. `moonmind.omnigent-execution-readiness.v3` advertises OpenCode only when the same selectors used by planning find a fresh, trusted, host-class-admissible, credential-compatible target and both gates allow it. Otherwise Workflow Create shows the specific `generic_realizer_not_ready` or qualification blocker while Provider Profile setup remains available.

## Generic realizer lifecycle (issue §7)

`generic-omnigent-host@1` reuses the proven generic parts of the existing profile-bound lifecycle:

- Provider Profile leases (deterministic order, release last)
- Host binding and lease (fenced)
- Workspace preparation and restore
- Repository credentials
- Resolved Skill delivery
- Mounted MoonMind tools
- Restricted egress
- Host registration waits
- Session creation and reattachment
- Event streaming and controls
- Capture and publication
- Checkpoint and remediation state
- Cancellation (removes partially created host + credential state)
- Janitor reconciliation (durable secret-free cleanup authority)
- Release-last ordering (Provider Profile lease after host+credential cleanup)

No separate OpenCode Temporal workflow; no duplication of the Codex coordinator.

## Operator diagnostics (no secret exposure)

```bash
# Image identity (secret-free)
docker inspect ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest> --format '{{.Id}} {{.RepoDigests}}'
```

Use Workflow Detail and the runtime binding's artifact references to inspect bounded catalog, image, host-registration, credential-mount, Skill, tool, egress, harness-readiness, and model-option attestations. Do not inspect `auth.json` or inject a test key into a worker filesystem. Materialization verification occurs inside the trusted writer/host boundary and records only structure, modes, ownership, and generation.

Check logs for:

- `OMNIGENT_HARNESS_BUILD_MISMATCH` — image/digest/implementation mismatch
- `OMNIGENT_VENDOR_RUNTIME_MISMATCH` — opencode missing or version outside range
- `OMNIGENT_CREDENTIAL_GENERATION_FENCED` — stale generation
- `OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED` — materializer failure or permission mismatch
- `OMNIGENT_MODEL_UNAVAILABLE` — selected `opencode-go/<model>` not in catalog
- `OMNIGENT_HOST_REGISTRATION_TIMEOUT` — the correlated host did not become fresh and online
- `OMNIGENT_RUNTIME_BINDING_CONFLICT` — stale lifecycle owner or replay conflict
- `OMNIGENT_CLEANUP_DEFERRED` — durable cleanup remains for the janitor

## Key rotation

- Rotation before first lease acquisition is allowed (no generation sticky yet)
- After runtime binding, generation is sticky; newer generation does not update binding
- Credential-maintenance lane fences/drains bound host+session before activating replacement state
- Retry reuses recorded generation; no silent adoption of newer generation
- Hosts and retries using older generation are fenced (`fencing_conflict`)

## Cleanup

- Stop or drain the session before removing its host.
- Remove the fenced host container and verify it no longer consumes credentials.
- Remove egress/runtime state, then the run-owned credential volume and workspace/Skill/tool projections according to retention policy.
- Persist terminal cleanup evidence before releasing Provider Profile leases in reverse acquisition order.
- The janitor claims stale nonterminal bindings with a new fencing generation and replays materializer-specific cleanup using secret-free handles. It never needs the raw key to remove a volume.
- Provider Profile capacity is always released last.

## Rollback

Existing Codex-through-Omnigent remains on `codex-profile-bound@1` and the mature OAuth host coordinator. There is no execution-time fallback from OpenCode to Codex. Disabling either generic feature gate or removing `OMNIGENT_OPENCODE_HOST_IMAGE_REF` makes OpenCode unlaunchable without affecting Codex.

## Build and publish

See `services/omnigent/opencode-host/Dockerfile` and `.github/workflows/docker-publish-opencode-host.yml`.

```
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg OMNIGENT_HOST_BASE_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:<base> \
  --build-arg OMNIGENT_BUILD_DIGEST=sha256:<shared-server-and-host-build-identity> \
  -f services/omnigent/opencode-host/Dockerfile \
  -t ghcr.io/moonladderstudios/omnigent-host-opencode:1.18.11 --push .
```

Provenance and digest are recorded as GHCR attestations and `docker buildx imagetools inspect`.
