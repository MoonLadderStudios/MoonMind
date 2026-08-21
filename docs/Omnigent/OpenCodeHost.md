# OpenCode Host (omnigent-host-opencode)

**Status:** Implemented (MoonLadderStudios/MoonMind#3752)  
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
payload: { "opencode-go": { "apiKey": "<secret>", "type": "api" } }
providerKey: opencode-go (verified against pinned CLI)
mount: read-only when compatible
cleanup: remove-owned-state (run-scoped)
```

Steps (issue §5):

1. Acquire Provider Profile lease
2. Record acquired generation in fenced runtime binding (sticky)
3. Resolve API-key SecretRef in trusted worker
4. Create lease-owned credential state
5. Write exact OpenCode credential structure
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

Never use `OPENCODE_AUTH_CONTENT`, CLI args, or ordinary Docker env for the production secret path.

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

1. Materialize proposed key to an isolated disposable credential directory using the same pinned `opencode` binary the host image uses
2. Query OpenCode's model catalog
3. Require at least one `opencode-go/<model-id>` result
4. Optionally run a minimal protected live inference when requested
5. Delete temporary credential state
6. Persist only normalized model metadata and validation evidence

Raw key is never returned after submission.

## Agent Profile

```
agentKind: external
agentId: omnigent
harness:
  id: opencode-native
  implementationRef: omnigent-harness-implementation:sha256:...
source:
  kind: upstream | bundle
  upstreamId: opencode-native-ui (or imported bundle)
credentialSlots:
  - id: primary-model
    acceptedAuthModels: [own-auth]
    acceptedProviderIds: [opencode, opencode-go]
hostClassRef: omnigent-opencode@1
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1
model:
  qualifiedId: opencode-go/<model-id>
  routeRef: opencode-go
```

Generic upstream harness and agent projection, Host Class and launch-policy selection, and model selection using qualified IDs are exposed in Workflow Create.

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

Readiness by harness name alone is insufficient.

## Deployment configuration

```
OMNIGENT_OPENCODE_HOST_IMAGE_REF=ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest>
# Fallbacks (mutable, not launch authority):
OMNIGENT_OPENCODE_HOST_IMAGE=ghcr.io/moonladderstudios/omnigent-host-opencode
OMNIGENT_OPENCODE_HOST_IMAGE_TAG=1.18.11
```

- Build from pinned Omnigent source/base image
- Publish to GHCR with provenance and digest evidence
- Make digest available to Host Class bootstrap via `get_opencode_host_image_ref()`
- Fail closed when only a mutable tag is configured
- Image is pulled lazily only when an OpenCode workflow requires it; cached layers are reused

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
# Host attestation (secret-free)
docker inspect ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:<digest> --format '{{.Id}} {{.RepoDigests}}'
python -c "from moonmind.omnigent.harness_platform import get_opencode_host_class; print(get_opencode_host_class().model_dump(by_alias=True, mode='json'))"

# Materializer evidence (secret-free handle)
python -c "
from moonmind.omnigent.harness_platform import materialize_opencode_auth_json
handle = materialize_opencode_auth_json(api_key='sk-test-...', provider_profile_ref='opencode-go-default', provider_lease_ref='lease:1', credential_generation=4, host_root='/tmp/test')
print(handle)  # no apiKey present
"

# Verify credential file (without printing contents)
python -c "
from moonmind.omnigent.harness_platform import verify_opencode_auth_file
print(verify_opencode_auth_file(host_root='/', expected_generation=4))
"
```

Check logs for:

- `OMNIGENT_HARNESS_BUILD_MISMATCH` — image/digest/implementation mismatch
- `OMNIGENT_VENDOR_RUNTIME_MISMATCH` — opencode missing or version outside range
- `OMNIGENT_CREDENTIAL_GENERATION_FENCED` — stale generation
- `OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED` — materializer failure or permission mismatch
- `OMNIGENT_MODEL_UNAVAILABLE` — selected `opencode-go/<model>` not in catalog

## Key rotation

- Rotation before first lease acquisition is allowed (no generation sticky yet)
- After runtime binding, generation is sticky; newer generation does not update binding
- Credential-maintenance lane fences/drains bound host+session before activating replacement state
- Retry reuses recorded generation; no silent adoption of newer generation
- Hosts and retries using older generation are fenced (`fencing_conflict`)

## Cleanup

- On-demand host: `--rm` or `remove` after harvest
- Credential state: `remove-owned-state` via `cleanup_opencode_auth(tmp_root, ...)`
- Janitor: reconciles abandoned `generic-omnigent-host@1` and materializer state using durable `credential-cleanup:...` authority (secret-free)
- Provider Profile lease: released last

## Rollback

Existing Codex-through-Omnigent path remains on `codex-profile-bound@1` with `omnigent-codex-current@1` and OAuth host runtime. Direct Codex remains available as migration fallback. Removing `OMNIGENT_OPENCODE_HOST_IMAGE_REF` makes OpenCode Workflows un-launchable without affecting Codex.

## Build and publish

See `services/omnigent/opencode-host/Dockerfile` and `.github/workflows/docker-publish-opencode-host.yml`.

```
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg OMNIGENT_HOST_BASE_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:<base> \
  -f services/omnigent/opencode-host/Dockerfile \
  -t ghcr.io/moonladderstudios/omnigent-host-opencode:1.18.11 --push .
```

Provenance and digest are recorded as GHCR attestations and `docker buildx imagetools inspect`.
