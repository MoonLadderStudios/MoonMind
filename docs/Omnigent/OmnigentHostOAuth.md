# Omnigent Host OAuth

**Status:** Desired-state design  
**Owners:** MoonMind Platform  
**Last updated:** 2026-07-11

**Implementation tracking:** rollout notes, spikes, and temporary handoffs belong under `docs/tmp/` or in issue/PR tracking. This document defines the durable target-state contract.

## Related documents

- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/ManagedAgents/OAuthTerminal.md`](../ManagedAgents/OAuthTerminal.md)
- [`docs/ManagedAgents/ClaudeAnthropicOAuth.md`](../ManagedAgents/ClaudeAnthropicOAuth.md)
- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)
- [`docs/MoonMindRoadmap.md`](../MoonMindRoadmap.md)

---

## 1. Purpose

MoonMind already provides first-party OAuth setup for Claude Code with Anthropic and Codex CLI with OpenAI through the Settings UI. Those flows write provider-managed credential state into durable Docker auth volumes and register connected Provider Profiles.

This document defines how the same Settings-created OAuth profiles become usable by **unchanged `omnigent-host` containers** without a second login ceremony, without extracting OAuth tokens, and without allowing several concurrent writers to one mutable OAuth identity.

The desired operator experience is:

```text
Settings -> Provider Profiles -> Connect OAuth
  -> MoonMind OAuth Session
  -> verified Claude or Codex OAuth volume
  -> connected Provider Profile with max_parallel_runs = 1
  -> MoonMind selects that profile for an Omnigent-backed workflow
  -> one profile-bound omnigent-host container
  -> one Omnigent session / runner
  -> Claude Code or Codex uses the existing OAuth login
```

The OAuth setup surface remains MoonMind Settings. Operators must not have to run `/login`, `claude auth login`, or `codex login` inside an ordinary Omnigent workflow session.

---

## 2. Scope

This document covers:

- Claude Code / Anthropic OAuth profiles created through MoonMind Settings;
- Codex CLI / OpenAI OAuth profiles created through MoonMind Settings;
- the Provider Profile concurrency invariant for mutable OAuth homes;
- profile-to-host credential-volume binding;
- static local Compose hosts as the first supported slice;
- MoonMind-launched profile-bound hosts as the managed target state;
- host/session routing, readiness, cleanup, reconnect, and disconnect behavior;
- the relationship between OAuth host use, the Omnigent bridge, and checkpoints;
- secret-safe diagnostics and durable references.

This document does not define:

- API-key Provider Profile materialization into Omnigent hosts;
- more than one concurrent host or runtime consumer for one OAuth identity;
- one Omnigent host multiplexing unrelated Provider Profiles;
- a custom MoonMind fork of `omnigent-host`;
- a new OAuth protocol or token broker;
- raw-token extraction from Claude or Codex credential files;
- the complete Omnigent bridge protocol, which belongs to `OmnigentBridge.md`;
- checkpoint and branching semantics in full, which belong to the checkpoint documents.

API-key profiles should eventually use the same host-launch and binding framework, but their secret materialization contract remains owned by Provider Profiles and the Secrets System rather than this OAuth-specific document.

---

## 3. Architectural decision summary

The target design is governed by these decisions:

1. **MoonMind Settings is the enrollment authority.** Claude and Codex OAuth are configured, repaired, reconnected, validated, and disconnected through the existing OAuth Session and Provider Profile systems.
2. **The Provider Profile is the durable selection identity.** Workflows select `executionProfileRef`; they do not select raw Docker volume names or paste credentials into `parameters.omnigent`.
3. **The OAuth volume is the canonical mutable credential store.** The selected CLI may refresh or rotate credential state while it runs, so the active profile-bound host must be able to persist authorized credential-home writes.
4. **OAuth Provider Profiles have a hard concurrency limit of one.** `credential_source = oauth_volume` implies `max_parallel_runs = 1` for the first-party Claude and Codex OAuth paths.
5. **The concurrency limit is global across execution substrates.** A direct MoonMind managed runtime and an Omnigent host must acquire the same Provider Profile lease. They must never use the same OAuth profile concurrently.
6. **One OAuth profile maps to at most one active Omnigent host container.** That host runs at most one active Omnigent session/runner while holding the profile lease.
7. **The host is profile-bound, not generic.** A Claude OAuth host mounts only the selected Claude profile credential home; a Codex OAuth host mounts only the selected Codex profile credential home.
8. **Omnigent server/host authentication is separate.** The credential used by `omnigent-host` to register with an Omnigent server or bridge is not the Anthropic/OpenAI OAuth credential.
9. **Only references cross durable boundaries.** Temporal history, bridge rows, checkpoints, diagnostics, and artifacts may carry profile, volume, binding, lease, host, and session references, but never OAuth access or refresh token values.
10. **Stock Omnigent compatibility is preserved.** The first supported topology configures and launches an unchanged upstream `omnigent-host`; MoonMind owns orchestration and policy around it.

---

## 4. Why OAuth concurrency is fixed at one

### 4.1 OAuth homes are mutable credential state

Claude Code and Codex CLI do not consume a permanently immutable bearer token file. Their OAuth homes may contain access tokens, refresh tokens, account metadata, session state, and runtime-managed files that the CLI updates over time.

A successful request may cause the CLI to:

- refresh an expired access token;
- rotate or replace a refresh token;
- rewrite a credential JSON file atomically or non-atomically;
- update account or organization metadata;
- migrate credential-file format between CLI versions;
- create locks, temporary files, or cached state used by later invocations.

MoonMind must not assume that two CLI processes can safely read, refresh, and rewrite one OAuth home concurrently. Even when an upstream provider happens to tolerate concurrent access tokens, the local file-update and refresh-token semantics remain provider- and CLI-version-specific.

### 4.2 The invariant is credential-wide, not host-local

The safety invariant is:

```text
For one OAuth Provider Profile / backing OAuth identity:

active direct managed consumers
+ active Omnigent host consumers
+ active credential-maintenance operations
<= 1
```

Restricting only the number of Omnigent hosts is insufficient. A direct Claude or Codex run overlapping an Omnigent run could still race on the same credential home. Both execution paths must acquire a lease from the same `ProviderProfileManager` runtime family.

### 4.3 Required enforcement points

For first-party OAuth profiles, MoonMind must enforce all of the following:

| Boundary | Required behavior |
| --- | --- |
| Settings OAuth start/finalize | Create or preserve the profile with `max_parallel_runs = 1`; reject a caller-supplied higher value. |
| Provider Profile create/update API | Reject `credential_source = oauth_volume` with `max_parallel_runs != 1` for the supported Claude/Codex OAuth contract. |
| Settings UI | Show OAuth concurrency as fixed at one rather than an editable capacity field. |
| Provider Profile Manager | Grant no more than one lease for the backing profile and share that ledger across direct and Omnigent execution. |
| Omnigent host binding | Allow at most one active host container for the profile. |
| Omnigent session creation | Allow at most one active session/runner on the profile-bound host. |
| Reconnect/disconnect | Require the profile to be drained or explicitly terminate the current run before mutating or invalidating the OAuth volume. |
| Migration/readiness | Normalize or block legacy OAuth profiles configured above one and surface an actionable diagnostic. |

A workflow, schedule, agent profile, or bridge request must not override this invariant.

### 4.4 Future relaxation

Concurrency greater than one is not forbidden forever, but it requires a separate, provider-specific design that proves safe refresh ownership. Examples include a supported token broker, immutable short-lived credential snapshots, or provider-documented multi-writer semantics with tested CLI behavior.

Until such a design ships, `max_parallel_runs = 1` is a hard contract rather than a conservative default.

---

## 5. Canonical Provider Profile shapes

The first-party OAuth profiles use the existing Provider Profile model.

### 5.1 Claude Code / Anthropic OAuth

```yaml
profile_id: claude_anthropic_oauth
runtime_id: claude_code
provider_id: anthropic
provider_label: Anthropic

credential_source: oauth_volume
runtime_materialization_mode: oauth_home
volume_ref: claude_auth_volume
volume_mount_path: /home/app/.claude

max_parallel_runs: 1
rate_limit_policy: backoff
cooldown_after_429_seconds: 900
enabled: true
auth_state: connected
last_auth_method: oauth_volume
```

### 5.2 Codex CLI / OpenAI OAuth

```yaml
profile_id: codex_openai_oauth
runtime_id: codex_cli
provider_id: openai
provider_label: OpenAI

credential_source: oauth_volume
runtime_materialization_mode: oauth_home
volume_ref: codex_auth_volume
volume_mount_path: /home/app/.codex

max_parallel_runs: 1
rate_limit_policy: backoff
cooldown_after_429_seconds: 900
enabled: true
auth_state: connected
last_auth_method: oauth_volume
```

Profile IDs and volume names may be deployment-configurable, but the selected profile, runtime, provider, credential source, and volume reference must agree. MoonMind must fail fast on a Claude profile bound to a Codex home or vice versa.

---

## 6. Runtime-neutral host credential contracts

The desired implementation separates the canonical credential identity from one concrete host mount.

### 6.1 `AuthVolumeRef`

```yaml
AuthVolumeRef:
  providerProfileId: claude_anthropic_oauth
  runtimeId: claude_code
  providerId: anthropic
  volumeRef: claude_auth_volume
  credentialGeneration: 7
  ownerUserId: user_123
```

`AuthVolumeRef` identifies the canonical OAuth backing store. It contains no credential body.

`credentialGeneration` changes when a successful reconnect or credential replacement makes existing host materialization stale. The generation is metadata, not a token version exposed by the provider.

### 6.2 `CredentialMountRef`

```yaml
CredentialMountRef:
  authVolumeRef: authvol_123
  targetPath: /home/app/.claude
  access: read_write
  uid: 1000
  gid: 1000
  runtimeId: claude_code
```

For the initial supported contract, the canonical OAuth volume is mounted read/write into the **exclusive profile-bound host**. Read/write access is required because the CLI may persist refresh or credential migration state.

A future copy-on-start design is valid only if it also defines exclusive refresh ownership and reliable atomic writeback or broker-managed replacement. A read-only seed that silently discards refreshed credentials must not become the default for long-lived OAuth hosts.

### 6.3 `OmnigentOAuthHostBinding`

```yaml
OmnigentOAuthHostBinding:
  bindingId: omnigent-oauth:claude_anthropic_oauth
  providerProfileId: claude_anthropic_oauth
  runtimeId: claude_code
  endpointRef: default
  harness: claude-native
  credentialMountRef: credmount_123
  hostMode: static_compose        # later: on_demand_docker
  maxHosts: 1
  maxSessionsPerHost: 1
```

The binding describes how a Provider Profile is made available to Omnigent. It is not a second Provider Profile and does not own credential setup.

### 6.4 `OmnigentHostLease`

```yaml
OmnigentHostLease:
  leaseId: ohl_123
  bindingId: omnigent-oauth:claude_anthropic_oauth
  providerProfileId: claude_anthropic_oauth
  providerLeaseId: provider-lease-workflow-id
  credentialGeneration: 7
  workflowId: workflow_123
  agentRunId: run_123
  containerId: container_abc
  omnigentHostId: host_def
  omnigentSessionId: conv_ghi
  state: assigned
  expiresAt: 2026-07-11T20:00:00Z
```

The host lease makes container/session ownership retry-safe. It stores only identifiers and safe lifecycle metadata.

---

## 7. Settings and OAuth lifecycle

### 7.1 Initial connection

When the operator selects **Connect OAuth** in Settings:

1. MoonMind creates an OAuth Session for the selected first-party profile.
2. The OAuth Session acquires exclusive credential-maintenance authority for the profile.
3. A short-lived auth runner mounts the canonical OAuth volume at the provider enrollment path.
4. The operator completes the provider's interactive login ceremony through the MoonMind terminal bridge.
5. MoonMind verifies the CLI credential state without returning credential contents.
6. MoonMind updates the Provider Profile to `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `auth_state = connected`, and `enabled = true`.
7. MoonMind fixes `max_parallel_runs = 1` and synchronizes the Provider Profile Manager.
8. MoonMind increments or initializes `credentialGeneration`.
9. The auth runner exits. The profile is now eligible for direct or Omnigent-backed execution.

### 7.2 Reconnect or repair

Reconnect is a credential mutation and therefore requires exclusive ownership.

MoonMind must either:

- wait until the active profile lease and host lease are released; or
- present an explicit operator action to interrupt/stop the active run, drain the host, and then reconnect.

After successful reconnect:

- increment `credentialGeneration`;
- mark any existing host lease using an older generation stale;
- stop or recycle stale hosts before new session assignment;
- keep `max_parallel_runs = 1`.

MoonMind must not reconnect the canonical volume while an active Claude or Codex process may be refreshing it.

### 7.3 Disconnect

Disconnect must:

- prevent new profile leases;
- stop or drain a profile-bound Omnigent host;
- mark the Provider Profile disabled/disconnected;
- invalidate the Omnigent host binding until OAuth is connected again;
- preserve or remove the backing volume according to the explicit disconnect policy;
- never silently fall back to an ambient API key.

### 7.4 Settings status projection

The Settings UI should distinguish:

```text
Not connected
OAuth connected and available
OAuth connected and in use
OAuth reconnect waiting for active run
OAuth validation failed
OAuth disconnected
Omnigent host starting
Omnigent host ready
Omnigent host failed auth preflight
```

The OAuth concurrency field should be displayed as **1 (fixed for OAuth)**.

---

## 8. Host materialization rules

### 8.1 Profile-bound host

An OAuth-enabled `omnigent-host` container must be bound to exactly one Provider Profile. It must not mount both Claude and Codex OAuth volumes, and it must not serve unrelated users or profiles.

The host receives four distinct state classes:

| State | Example target | Lifecycle |
| --- | --- | --- |
| Omnigent host identity/state | `/home/app/.omnigent` | host-specific; never shared with another profile-bound host identity |
| Provider OAuth home | `/home/app/.claude` or `/home/app/.codex` | canonical profile credential volume; exclusive read/write use |
| Workspace | `/workspaces/<run>` | workflow/session-scoped and policy-controlled |
| Temporary/runtime support | `/tmp`, run support paths | host/run-scoped and removable |

Omnigent state and provider OAuth state must never share one volume.

### 8.2 Claude Code environment

A Claude OAuth host uses the profile mount path consistently:

```text
HOME=/home/app
CLAUDE_HOME=/home/app/.claude
CLAUDE_VOLUME_PATH=/home/app/.claude
CLAUDE_CONFIG_DIR=/home/app/.claude
```

The host must clear competing credential variables, including the profile's complete `clear_env_keys` set. Typical first-party OAuth blockers include:

```text
ANTHROPIC_API_KEY
ANTHROPIC_AUTH_TOKEN
CLAUDE_API_KEY
CLAUDE_CODE_OAUTH_TOKEN
OPENAI_API_KEY
```

`$HOME/.claude.json` is host/session configuration and workspace-trust state, not the canonical OAuth volume contract. It must be managed separately from the OAuth credential home.

### 8.3 Codex CLI environment

A Codex OAuth host uses:

```text
HOME=/home/app
CODEX_HOME=/home/app/.codex
CODEX_CONFIG_HOME=/home/app/.codex
CODEX_CONFIG_PATH=/home/app/.codex/config.toml
```

The host must clear profile-defined competing credentials and custom-provider overrides when OpenAI OAuth is selected. Typical blockers include:

```text
OPENAI_API_KEY
CODEX_ACCESS_TOKEN
OPENAI_BASE_URL
```

Provider Profile materialization remains authoritative for any allowed Codex config overlay. The host must not rewrite the profile to a different provider because an ambient environment variable is present.

### 8.4 Ownership and permissions

The auth runner, profile-bound host, and runner process must agree on the runtime user. The supported local container convention is UID/GID `1000:1000` with `HOME=/home/app`.

Initialization must be automatic and idempotent:

- create state directories;
- set the expected owner/group;
- apply restrictive permissions compatible with the CLI;
- refuse symlink/path traversal outside the mounted credential root;
- avoid recursively changing ownership on an unrelated volume;
- fail before host registration when the mount is missing or unusable.

### 8.5 Credential preflight

Before the host is eligible for assignment, MoonMind must run the runtime-specific preflight inside the exact host environment:

```text
Claude: claude auth status
Codex:  codex login status, or the canonical registered verifier
```

The result must be reduced to safe readiness metadata. Raw command output is not durable evidence unless redacted and explicitly approved.

---

## 9. Selection, leasing, and session launch

### 9.1 Request shape

An Omnigent-backed run continues to use the canonical external identity while selecting the Provider Profile explicitly:

```json
{
  "agentKind": "external",
  "agentId": "omnigent",
  "executionProfileRef": "claude_anthropic_oauth",
  "parameters": {
    "omnigent": {
      "agent": {
        "agentName": "claude-native-ui"
      }
    }
  }
}
```

The request does not contain a Docker volume name, OAuth token, refresh token, or manually copied host ID.

### 9.2 Resolution order

Before creating an Omnigent session, MoonMind must:

1. resolve the exact Provider Profile or selector result;
2. verify that it is enabled, connected, launch ready, and OAuth-backed;
3. verify the requested Omnigent agent/harness matches the profile runtime;
4. acquire the profile's single `ProviderProfileManager` lease;
5. resolve or create the `OmnigentOAuthHostBinding`;
6. ensure there is no other active host for the binding;
7. start or reuse the one profile-bound host according to deployment mode;
8. validate credential generation, mount, CLI auth, host registration, and harness readiness;
9. create the Omnigent session on that exact host;
10. persist the bridge/session/host lease references before posting the first message.

If any step fails, MoonMind must not fall back to another credential method or generic host silently.

### 9.3 Static Compose slice

The first local-development slice may use one dedicated Compose service per first-party OAuth profile:

```text
omnigent-host-claude
omnigent-host-codex
```

Compose and init scripts should automatically provide:

- the correct OAuth volume mount;
- separate Omnigent state volume;
- correct UID/GID and home variables;
- credential-env clearing;
- runtime auth preflight;
- stable host identity;
- host registration readiness;
- restart behavior.

MoonMind must discover the configured binding and host ID. Operators should not extract host IDs or manually edit workflow JSON.

This static slice is a trusted local/single-tenant transitional topology. The profile lease still controls session assignment, and each host runs at most one active runner.

### 9.4 On-demand managed slice

The managed target state launches the profile-bound host only after acquiring the profile lease:

```text
Acquire profile lease
  -> launch host container with exact OAuth mount
  -> wait for bridge/server registration
  -> create one session
  -> harvest terminal evidence
  -> stop/remove host
  -> release profile lease
```

One Provider Profile lease corresponds to one host lease and one active Omnigent session. This simple one-to-one model is the required first production topology.

---

## 10. Omnigent bridge relationship

The bridge is the required session communication and observability boundary after OAuth host materialization works.

The bridge must:

- resolve `executionProfileRef` to the profile-bound host rather than accepting an arbitrary host choice;
- enforce the Provider Profile lease before session creation;
- persist safe profile, binding, host lease, host, and session references;
- expose failed host/auth/session startup in Workflow Detail even when no normal stream starts;
- normalize session events and harvest resources into MoonMind artifacts;
- prevent manual or alternate bridge routes from bypassing the one-profile/one-session rule.

A profile-bound OAuth host must not be treated as a general-purpose host in an unrestricted Omnigent host picker. Direct selection through a stock Omnigent UI is unsupported unless that route participates in the same MoonMind lease and authorization checks.

Host authentication remains separate:

```text
omnigent-host -> Omnigent server/bridge credential
Claude/Codex -> Anthropic/OpenAI OAuth volume
```

Neither credential may substitute for the other.

---

## 11. Lifecycle, retries, and cleanup

### 11.1 Idempotent startup

A retry must first inspect durable bridge and host-lease state.

For the same idempotency key, MoonMind should:

- reattach to the existing registered host/session when still valid;
- finish a partially completed readiness sequence;
- replace a failed/stale host only after marking the old lease draining or failed;
- never launch a second host while the first may still own the profile lease;
- never post the first message twice.

### 11.2 Terminal cleanup

After terminal evidence is harvested, MoonMind must:

1. interrupt or stop the Omnigent session as needed;
2. collect final stream/resource/diagnostic artifacts;
3. stop and remove an on-demand host, or mark a static host idle;
4. verify no runner remains attached to the OAuth home;
5. persist terminal host/session state;
6. release the host lease;
7. release the Provider Profile lease.

The profile lease is released last so another consumer cannot start while cleanup or credential writes are still in progress.

### 11.3 Crash and stale-lease recovery

The host/container janitor and Provider Profile Manager must cooperate:

- a terminated workflow cannot hold the OAuth profile indefinitely;
- a live host with a lost workflow owner is drained before lease reclamation;
- an absent container with a durable lease is marked failed and released;
- an old credential generation invalidates host reuse;
- lease recovery is observable and auditable;
- cleanup remains idempotent across Temporal retries and worker restarts.

### 11.4 Provider failure and cooldown

A provider-attributed 429 or quota failure should:

- be recorded against the selected Provider Profile;
- trigger its existing cooldown policy;
- stop or drain the session/host after evidence capture;
- release the active lease;
- prevent immediate retry on the same profile until policy permits it.

A credential-invalid response should fail closed, surface **Reconnect OAuth**, and transition the profile to the appropriate validation-failed or disconnected state after confirmation.

---

## 12. Checkpoint relationship

Checkpoints must not depend on the continued existence of a particular host container.

A checkpoint for an Omnigent OAuth session may safely reference:

```text
providerProfileId
credentialGeneration
omnigentEndpointRef
omnigentHostBindingRef
omnigentHostLeaseRef
omnigentHostId
omnigentSessionId
bridgeSessionId
externalStateRef
idempotencyKey
workspace/diff/artifact refs
terminal and diagnostics refs
```

It must not contain credential files or token values.

Resume has two modes:

1. **Live reattach:** the same session and host are still valid, the profile lease is still owned, and the bridge can prove identity and idempotency.
2. **Cold restore:** MoonMind acquires the same Provider Profile again, launches or selects a new profile-bound host, and starts a fresh Omnigent session from validated checkpoint artifacts and corrected instructions.

Branching always creates a new session and host lease. It does not mutate the original session or reuse its credential lease concurrently.

---

## 13. Security model

The following boundaries are mandatory:

- OAuth hosts are dedicated to one Provider Profile and one runtime family.
- One host must not mount both `claude_auth_volume` and `codex_auth_volume`.
- One OAuth profile has one active runtime consumer across all MoonMind execution paths.
- The canonical OAuth volume is mounted only into trusted auth runners and profile-bound runtime hosts.
- The host container and every runner inside it are considered able to read and modify the mounted OAuth home.
- No unrelated tenant, workflow, or manually selected Omnigent session may target that host.
- Raw OAuth credentials never enter Temporal history, request parameters, bridge rows, checkpoints, logs, diagnostics, or artifacts.
- Credential variables that would override the intended OAuth path are cleared before launch.
- Host identity/state is stored separately from provider OAuth state.
- Omnigent host/server credentials are resolved separately from provider OAuth.
- Docker daemon administrators remain privileged and can inspect or mount host volumes; this topology does not defend against a malicious Docker administrator.
- Multi-tenant or multi-profile sharing of one host waits for a stronger per-runner isolation boundary, such as runner containers or mount namespaces.

---

## 14. Diagnostics and observability

MoonMind should expose secret-safe diagnostics for these stages:

```text
profile_resolution
profile_lease_wait
host_binding_resolution
container_start
credential_mount
credential_preflight
host_registration
harness_readiness
session_creation
session_running
resource_harvest
host_cleanup
profile_lease_release
```

Safe diagnostic fields include:

- Provider Profile ID, runtime ID, and provider ID;
- credential source and materialization mode;
- OAuth volume reference and expected mount path;
- credential generation;
- profile lease and host lease identifiers;
- container and Omnigent host identifiers;
- bridge and Omnigent session identifiers;
- readiness state, timestamps, and bounded redacted error summaries;
- whether cleanup and lease release completed.

Unsafe fields include:

- credential file contents;
- access or refresh tokens;
- pasted authorization codes;
- unredacted environment dumps;
- raw OAuth home listings when filenames or paths may disclose sensitive metadata.

Workflow Detail should show enough lifecycle evidence to answer:

```text
Which Provider Profile was selected?
Why did the run wait?
Which host was bound?
Did OAuth preflight pass?
Did the host register?
Which Omnigent session ran?
Was the host cleaned up and the profile lease released?
```

---

## 15. Acceptance criteria

The desired state is satisfied when all of the following are true:

1. A user can connect Claude OAuth in Settings and later run `claude-native` through Omnigent without another login.
2. A user can connect Codex OAuth in Settings and later run `codex-native` through Omnigent without another login.
3. OAuth setup and Provider Profile APIs enforce `max_parallel_runs = 1`.
4. A direct managed run and an Omnigent-backed run cannot consume the same OAuth profile concurrently.
5. A second Omnigent request for the same OAuth profile queues or fails according to the Provider Profile rate-limit policy; it never launches a second host.
6. The profile-bound host exposes only the selected runtime's OAuth home and clears competing credentials.
7. The active CLI can persist refresh-state updates to the canonical OAuth volume while it holds the exclusive lease.
8. Reconnect and disconnect drain active hosts or require explicit operator termination before credential mutation.
9. Workflow requests select the Provider Profile, while MoonMind resolves the host binding and host ID automatically.
10. Temporal retries and worker restarts do not duplicate hosts, sessions, or first messages.
11. Terminal evidence is harvested before host cleanup and remains available after the container is gone.
12. Checkpoints carry only safe refs and can cold-restore onto a newly launched profile-bound host.
13. Wrong-runtime volumes, stale credential generations, missing mounts, invalid OAuth, and host-registration failures produce actionable diagnostics.
14. No OAuth token or credential body appears in workflow history, bridge persistence, logs, artifacts, checkpoints, or API responses.

---

## 16. Delivery sequence

The implementation order is intentional:

1. **Omnigent host OAuth:** prove that Settings-created Claude and Codex OAuth profiles can drive one profile-bound static host with the global concurrency-one invariant.
2. **Omnigent bridge:** make profile-aware session creation, events, chat projection, resources, and failure diagnostics reliable against that host.
3. **On-demand host containers:** replace manual/static provisioning with retry-safe workflow-requested host launch and cleanup while preserving the same profile/binding/bridge contracts.
4. **Omnigent session checkpointing:** capture bridge external state and restore onto a live or newly launched profile-bound host without depending on container-local mutable state.

This sequence keeps credential correctness ahead of automation, communication semantics ahead of dynamic provisioning, and durable recovery after both runtime and bridge identity are stable.
