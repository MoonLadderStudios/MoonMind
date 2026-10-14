# Provider Profiles

**Related design documents:** [SecretsSystem.md](./SecretsSystem.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md), [Codex via Omnigent Create-to-host contract](../Omnigent/CodexCreateToHostContract.md), [Provider Profile Model and Effort Tiers](./ProviderProfileModelEffortTiers.md), [Provider Profile Tier Settings](../UI/ProviderProfileModelEffortTierSettings.md), [Provider Profile Creation](../UI/ProviderProfileCreation.md)

Omnigent compatibility and capacity are governed by an
[immutable policy version](../Omnigent/PolicyAuthority.md). The Codex product path
a profile serves is the external-agent identity `agentKind=external`,
`agentId=omnigent`, nested harness `codex-native`, described end-to-end in
[NormalCodexProductPathReconciliation.md](../Omnigent/NormalCodexProductPathReconciliation.md).

A Provider Profile is the durable connection and capacity identity for a managed
launch. It records which Harness and upstream Provider a connection supports,
the credential source and the secret references or OAuth volume behind it, how
provider configuration is materialized into the runtime, concurrency and
cooldown policy, model/effort tiers, and whether Settings has activated it.

Secret storage and resolution belong to [SecretsSystem.md](./SecretsSystem.md),
browser OAuth transport to [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md),
the Settings form to [Provider Profile Creation](../UI/ProviderProfileCreation.md),
and the tier editor to
[Provider Profile Tier Settings](../UI/ProviderProfileModelEffortTierSettings.md).

---

## 1. Summary

Managed Harnesses do not map one-to-one to a provider or an auth method.
`claude_code` can run against Anthropic via OAuth or API key, or against MiniMax
or Z.AI through Anthropic-compatible environment shaping. `codex_cli` can run
against OpenAI via OAuth or API key, or against MiniMax through config plus
environment materialization. A Provider Profile answers the full question: which
Harness launches against which provider, with which credential source,
materialized how, under which concurrency and cooldown policy.

Profiles are visible in Settings before credentials exist but are not launchable
until credentials are verified. Disabled-by-default is a security control for
unconfigured providers, not an extra step after setup: successful user-initiated
setup enables the profile.

A fresh installation with no provider credentials persists exactly
`claude_anthropic_oauth` and `codex_openai_oauth`, both disabled and not
configured. API-key and alternative-provider profiles are added and enabled only
when their credentials are configured.

---

## 2. Responsibilities and Boundaries

| System | Owns |
| --- | --- |
| Provider Profiles | Harness ownership, provider selection, routing metadata, tier policy, credential source class, materialization, launch shaping, concurrency and cooldown, `enabled`, `auth_state`, `disabled_reason` |
| [Secrets System](./SecretsSystem.md) | `SecretRef` semantics, backends, encryption, key custody, launch-time resolution, audit, rotation, proxy-first handling |
| [OAuth Terminal](../ManagedAgents/OAuthTerminal.md) | PTY/WebSocket sessions, OAuth session lifecycle, auth containers, verification and registration of OAuth-backed profiles |
| Settings | Provider discovery and setup on `/settings/providers-secrets` |

A profile stores references to secrets but does not define `SecretRef`; it may be
written by the OAuth session workflow but never stores terminal transport details.

Settings is the one surface that is not Harness-scoped: it offers an **All
harnesses** view plus a Harness filter, computes configuration health from the
unfiltered collection, and submits canonical `runtime_id` values. Execution
surfaces are Harness-scoped (section 8.4).

---

## 3. Key Concepts

### 3.1 Harness vs Provider

A **Harness** is the agent software (`claude_code`, `codex_cli`, `opencode`),
stored as `runtime_id`; that field is not a Backend selector. **Backend** is the
system managing the Harness, normally Omnigent. A **Provider** is the upstream
service (`anthropic`, `openai`, `minimax`, `zai`). They are separate identities:
`claude_code + minimax` and `codex_cli + minimax` are both valid.

### 3.2 Credential source and materialization

`credential_source` is `oauth_volume` (a mounted auth volume managed outside the
row), `secret_ref` (resolved from the Secrets System at launch), or `none` (no
secret, or setup not completed). The concrete secret backend is a Secrets System
concern.

`runtime_materialization_mode` describes how the runtime is prepared:
`oauth_home` (mount volume, set home variables), `api_key_env` (a few secret
environment variables), `env_bundle` (a provider-specific environment block),
`config_bundle` (generated config files), or `composite`.

### 3.3 Secret roles

A profile names the secret roles a launch needs (`anthropic_api_key`,
`openai_api_key`, `provider_api_key`, …) and binds each to a `SecretRef` in
`secret_refs`; templates refer to roles with `from_secret_ref`.

<a id="55-default-model-ownership"></a>

### 3.4 Default model ownership

Model and effort policy lives in `model_tiers` and `default_model_tier`, a
one-based tier index rather than a separate model/effort pair. Null tier fields
mean the compatible runtime default; full-pair Custom intent follows the
[canonical resolution contract](./ProviderProfileModelEffortTiers.md#92-resolution-order).
The schema and API still carry scalar `default_model` and `default_effort`;
pre-tier rows are converted into tiers (`moonmind/provider_profiles/model_tiers.py`),
seeding writes tiers, and materializers consume the resolver's result rather
than the scalars. Runs keep the concrete values resolved at launch.

### 3.5 Enabled vs launch ready

`enabled` is durable operator intent. `launch_ready` is computed:

```text
launch_ready = enabled
  AND auth_state == connected
  AND credential bindings are valid (OAuth volume present / SecretRefs resolve)
  AND provider-specific validation and policy do not block launch
```

Profiles without `auth_state` fall back to credential-source checks.

---

## 4. Provider Profile Model

### 4.1 Contract

```yaml
ManagedAgentProviderProfile:
  profile_id, runtime_id, provider_id:  str
  provider_label, account_label:        str | null
  credential_source:             oauth_volume | secret_ref | none
  runtime_materialization_mode:  oauth_home | api_key_env | env_bundle | config_bundle | composite
  enabled, is_default, default_selected_by_operator: bool
  tags: [str]
  priority: int
  auth_state:       not_configured | oauth_pending | api_key_pending | connected | validation_failed | disconnected
  disabled_reason:  missing_credentials | auth_invalid | user_disabled | policy_disabled | disconnected | null
  first_authenticated_at, last_validated_at: timestamp | null
  last_auth_method: str | null
  model_tiers: [ProviderModelEffortTier]   # non-empty
  default_model_tier: int                  # one-based
  model_overrides: dict[str, str]
  max_parallel_runs, cooldown_after_429_seconds, max_lease_duration_seconds: int
  rate_limit_policy: backoff | queue | fail_fast
  volume_ref, volume_mount_path: str | null
  secret_refs: dict[str, SecretRef]
  clear_env_keys: [str]
  env_template: dict[str, object]
  file_templates: [{path, format, merge_strategy, content_template, permissions}]
  home_path_overrides: dict[str, str]
  command_behavior: dict[str, object]
  owner_user_id, created_at, updated_at
```

The table is `managed_agent_provider_profiles` (`api_service/db/models.py`).
Defaults: `enabled` false, `auth_state` `not_configured`, `priority` 100,
`max_parallel_runs` 1, `cooldown_after_429_seconds` 300, `rate_limit_policy`
`backoff`, `max_lease_duration_seconds` 7200.

### 4.2 Field semantics

- **`is_default`** marks a runtime's default. A disabled or not-launch-ready
  profile never becomes default; with no launch-ready profile, normalization
  clears the default.
- **`default_selected_by_operator`** records *why* a profile is default. An
  operator choice (`is_default`, or `make_default` on a credential endpoint) sets
  it; automatic reconciliation (seeding, enrollment) only settles an unclaimed
  default. Default normalization is the only writer: automation never takes the
  default from a launchable profile with an operator claim, the profile that
  loses `is_default` loses the claim, and when nothing is launch-ready the
  default clears but the claim survives, because transient unlaunchability is
  not a revocation of intent.
- **`disabled_reason`** explains `enabled: false` and protects user intent;
  background work does not convert `user_disabled` to enabled.
- **`clear_env_keys`** are blanked before launch so a run cannot fall back to
  another provider or auth path.
- **`env_template`** values are literals or `from_secret_ref` placeholders.
- **`command_behavior`** holds profile-dependent strategy hints such as
  `suppress_cli_model_flag_when_env_model_present`,
  `default_codex_profile_name`, `auth_actions`, and `auth_readiness`.
- **`priority`**: higher is preferred; ties go to the most free slots
  (convention: 100 normal, 110–130 preferred, 50–90 fallback).

---

## 5. Settings-First Activation

### 5.1 Outcomes

| Event | Result |
| --- | --- |
| Fresh install | Claude and Codex OAuth cards visible, disabled, setup required |
| OAuth completes or a valid API key is added | Connected and enabled |
| User disables a connected profile | Stays disabled until the user enables it |
| Validation fails | Disabled with a readiness error |
| Policy blocks the provider | May be connected; launch stays blocked |

### 5.2 Seeded profiles

`claude_anthropic_oauth` and `codex_openai_oauth` are always seeded as disabled
setup affordances (`credential_source: none`, `disabled_reason:
missing_credentials`, no secrets or volume, `command_behavior.auth_actions`
listing setup actions). When `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set at
startup, `claude_anthropic_api` or `codex_openai_api` is also seeded, connected
and enabled. Custom profiles are created disabled unless the request is part of
a verified setup flow.

### 5.3 User-initiated activation vs passive validation

**Connect OAuth**, **Reconnect OAuth**, **Add API key**, and a successful
**Rotate API key** are setup actions. On success MoonMind sets `auth_state:
connected`, `disabled_reason: null`, `enabled: true`, and records
`first_authenticated_at` (if unset), `last_validated_at`, and `last_auth_method`.
Enabling is correct because the user is configuring the provider to use it; such
an action may also clear `user_disabled`.

Passive validation — background checks, migrations, repair jobs, readiness
refreshes, health probes, manager sync — may update `auth_state`, timestamps,
and diagnostics but never re-enables a `user_disabled` profile.

### 5.4 Disable, disconnect, failure, and policy states

| Situation | `enabled` | `disabled_reason` | `auth_state` |
| --- | --- | --- | --- |
| Manual disable | false | `user_disabled` | `connected` |
| Disconnect (clears volume binding, and `secret_refs` when removal is requested) | false | `disconnected` | `disconnected` |
| Validation failure | false | `auth_invalid` | `validation_failed` |
| Policy block | false, or true with `launch_ready` false | `policy_disabled`, or null | unchanged |

The second policy form is preferable for temporary policy because it keeps user
intent separate from enforcement.

### 5.5 API enforcement

Endpoints do not trust client-supplied `enabled=true`. A setup action enables
only when credentials are verified; otherwise `user_disabled` stays disabled and
an enable requires `launch_ready`. A patch enabling a profile with readiness
blockers fails with "Provider profile cannot be enabled until OAuth or API-key
credentials are verified."

---

## 6. OAuth-Backed Profiles

The profile stores the durable result of an OAuth login: `oauth_volume` /
`oauth_home`, `volume_ref`, `volume_mount_path`, `account_label`, and activation
state. PTY identifiers, WebSocket URLs, session ids, browser status, and OAuth
tokens belong to the OAuth session subsystem or the provider's files in the
volume, never the row.

Flow: Settings starts an OAuth session; MoonMind creates a short-lived auth
container with the target volume; the dashboard attaches through the PTY bridge
where required; the CLI drives login; MoonMind verifies credential state in the
volume, creates or updates the profile with the 5.3 activation state, and tears
down the container and session. A failed verification sets the validation-failure
state. Disconnect clears the volume binding; when an API-key credential also
exists, Settings may offer switching to it.

---

## 7. API-Key-Backed Profiles

### 7.1 Setup flow

Keys enter through `POST /provider-profiles/{profile_id}/credentials/api-key`
(`api_key`, optional `account_label`, `make_default`, `enable_after_validation`).
The server checks `provider_profiles.write`, validates the key with the
provider, stores it in the Secrets System, writes only a `SecretRef`, populates
`env_template`, `clear_env_keys`, and command behavior, applies the 5.3
activation state, and returns readiness, never the key. The candidate key is
never persisted in payloads, rows, diagnostics, audit rows, or artifacts.

First-party mappings: `claude_code + anthropic` sets `ANTHROPIC_API_KEY` from
role `anthropic_api_key` and clears `ANTHROPIC_AUTH_TOKEN` and
`ANTHROPIC_BASE_URL`; `codex_cli + openai` sets `OPENAI_API_KEY` from
`openai_api_key` and clears `MINIMAX_API_KEY`.

### 7.2 Credential-maintenance lease failures

Enrollment first acquires an exclusive maintenance lease (9.4). Failing to
acquire it happens before validation: RPC failures and timeouts return HTTP 503
`provider_credential_manager_unavailable`, credentials and readiness are
unchanged, and the key is not called invalid. Because Temporal may have accepted
the request before the result was lost, the 503 echoes `retry_idempotency_key`
(the `operation_id` the lease owner was derived from: `Idempotency-Key`, else
`X-Request-ID`, else generated). Retrying with it reattaches to the same owner
instead of orphaning a competing one.

The post-acquisition host drain is bounded at 120 seconds; a drain that never
answers is cancelled and returns the same 503 with `refusal:
credential_host_drain_timeout`. Rotation stays atomic.

While validating, the Settings drawer polls
`GET /provider-profiles/{profile_id}/credential-maintenance-status?idempotency_key=<key>`,
which exposes only counts and the caller's own `waiter_position`, and reports
`known: false` when the manager is unavailable. The drawer always sends a
client-generated `Idempotency-Key`. Cancelling or the 10-minute client timeout
only stops waiting; the server may still commit, so the outcome is shown as
uncertain and the retry reuses the key.

---

## 8. Request and Selection

### 8.1 Unified selection

[Harness, Provider Profile, and Backend Selection](../UI/HarnessProfileBackendSelection.md)
owns the vocabulary. A profile may pin an `execution_configuration` (immutable
id, version, digest). Submissions supply the profile id; admission resolves
compatible execution behavior and records an immutable snapshot that
continuations reuse. Selection does not change when model discovery expires or
a host image changes; credentials, trust, compatibility, and actual-host model
verification remain launch gates.

An omitted Backend resolves to the deployment default, normally Omnigent. A
profile without a compatible configuration reports the gap rather than falling
back to a direct launcher; an alternative Backend is an explicit, disclosed
Advanced choice. A new unconfigured draft may use an authorized default or sole
compatible profile. After a Harness change the profile is kept only if
compatible; MoonMind never substitutes another account. Disabled and
disconnected profiles remain visible with a setup action.

### 8.2 Request contract and resolution order

A request naming only `agent_id` is ambiguous once several providers exist, and
MoonMind does not route to whichever provider has a free slot.

```yaml
AgentExecutionRequest:
  agent_id: str
  execution_profile_ref: str | null
  profile_selector: {provider_id, tags_any, tags_all, runtime_materialization_mode}
```

1. `execution_profile_ref` resolves exactly that profile; busy or cooling
   profiles wait rather than permitting another account.
2. Otherwise resolution stays in the authorized Harness scope (`runtime_id ==
   agent_id`, or the Omnigent configuration's compatibility projection).
3. Filter by provider and tags; exclude disabled, not-ready, cooling, and full
   profiles.
4. Pick the highest priority, then most free slots.

Priority ordering applies only inside an operator-authorized pool; no
constraint does not mean "any launch-ready provider." Missing authority reports
the affected action instead of falling back across provider, account, or
Backend.

### 8.3 Omnigent compatibility

Omnigent is a facade, not a profile owner, so selection never matches
`runtime_id == omnigent`. Traditional execution profiles filter by the target's
`providerRuntime` and provider requirements. A generic (v2) Agent Profile
accepts a profile only when the materializer for its `(runtime_id,
provider_id)` pair is accepted by the selected harness under every allowed
launch policy; the readiness projection's `compatibleProviderProfiles` uses the
same check, so the API cannot accept a pair the UI calls incompatible.

### 8.4 Harness-owned selection scope

A profile is never multi-runtime because it owns runtime-specific
materialization, command behavior, tiers, capacity, and default selection. One
provider used by two runtimes is two profiles, which may share a managed secret.
Execution and authoring surfaces offer no **All harnesses** option, scope
results server-side with `GET /api/v1/provider-profiles?runtime_id=<harness>`
plus the Backend projection, key caches by the selected identity so stale
placeholder data is never submittable, and show a named setup state when a
Harness has no eligible profile.

### 8.5 Server-side correctness boundary

Every authoring and submission path rejects `selected_profile.runtime_id !=
effective_runtime_id` with `409 provider_profile_runtime_mismatch`, naming both
sides. The rule lives once in `api_service/services/provider_profile_runtime.py`.
A blank `runtime_id` is rejected (it would be launchable everywhere), and a
payload whose runtime fields disagree is validated against every runtime it
names, using the worker's precedence (`workflow.runtime.mode` first).

Enforcement sits at `TemporalExecutionService.create_execution`, which
submission, rerun, continuation, checkpoint branching, and deployment
operations all reach; recovery destinations inherit the source's pair and get
the same 409. Recurring schedules validate on create and update because they
persist a target before any run. This matters because the launcher picks its
strategy from the profile's `runtime_id`: a mismatched pair would silently run
a different, separately billed runtime.

---

## 9. Provider Profile Manager

### 9.1 Scope

`MoonMind.ProviderProfileManager` runs as one singleton per runtime family
(`provider-profile-manager:<runtime>`), so history growth and Continue-As-New
are independent per runtime. It owns active leases, per-profile capacity,
cooldowns, queued requests, and assignments, and receives only enabled,
launch-ready profiles. Each profile has its own `max_parallel_runs` budget and
capacity scope `provider-profile:<profile_id>` unless explicitly assigned a
shared scope, so one full profile does not block another.

Signals: `request_slot` and `release_slot` and `report_cooldown` (AgentRun →
Manager), `sync_profiles` and `shutdown` (System → Manager), `slot_assigned`
(Manager → AgentRun). Releases quote the lease id, purpose, and fencing
generation.

### 9.2 Waiting and cooldown

A run with no available profile waits durably in `awaiting_slot` or fails fast
per request policy. A full profile is busy, not unavailable: work queues
regardless of `rate_limit_policy`, and launch surfaces keep it selectable.
Waiting summaries name the runtime, requested provider or profile, and whether
the gap is capacity, cooldown, policy, or setup.

Plan-bound Codex and generic-host Omnigent runs acquire capacity in the workflow
before scheduling the execution Activity, so queue time spends neither a worker
slot nor the execution timeout. The workflow releases the lease after the Codex
coordinator confirms host cleanup; failed cleanup keeps capacity spent for
verified janitor teardown.

On an attributable 429 or quota exhaustion, AgentRun reports cooldown, releases
its slot, and re-requests with the same selector or exact profile. MoonMind
keeps the requested model and credential and retries after cooldown with bounded
backoff (three retries by default), then returns the structured provider failure.
Strategies trigger this by classifying rate limits as
`failure_class="integration_error"` with `provider_error_code="429"`.

### 9.3 Lease modes

One trusted classifier (`moonmind/provider_profiles/lease_client.py`) assigns a
mode from the declared purpose and the profile's credential contract, so no
caller can widen its own authority.

| Mode | When | Effect |
| --- | --- | --- |
| `shared_execution` | execution | consumes one of `N` slots |
| `single_flight_validation` | `credential_validation` with `credential_source: none` | one holder per evidence identity; no execution slot |
| `exclusive_maintenance` | other maintenance | blocks new consumers, drains credential consumers |

A mutable OAuth home is shared credential state, so validating it is exclusive
and the profile is capacity one. Exclusive maintenance drains only leases
holding the credential it touches, and only purposes that spend upstream
capacity wait on shared-scope fullness or cooldown; repair, revocation, and
OAuth login do not, so a saturated scope never blocks fixing a credential or
signing in.

### 9.4 Fairness and validation identity

Pending exclusive maintenance is a durable queue of owner records, not a count,
because fairness depends on owners. Admission stops at the queue head so
execution cannot starve maintenance. The queue survives Continue-As-New in
order: a waiting handler fails with `ProviderProfileManagerRollover` but keeps
its entry, and `ProviderProfileLeaseClient` resubmits to the successor at the
same position. Handlers arriving with a successor's first task wait for
`startup_restored` so nothing is misreported or double-granted.

A validation lease is keyed by a versioned evidence identity (probe version,
profile, image digest, credential generation, materializer, route, model). One
owner probes; joiners see `already_held` and stand down; reusing an owner ID
under another identity fails with `ProviderProfileLeaseIdentityConflict`; and an
abandoned probe expires at the shorter validation bound.

### 9.5 The durable lease ledger

The `provider_profile_slot_leases` row decides whether a slot is spent;
`provider_profile.sync_slot_leases` is its only writer.

- **States**: `held`, `cleanup_requested` (still spent, cleanup owed), and
  `released` (a fencing tombstone until purged). Anything else is unreconciled,
  not free.
- **Identity**: each row stores the lease, owner, purpose, compatibility class,
  plan and evidence identity, and admitted generations, as opaque untruncated
  text. Lease IDs are unique per runtime, because one workflow may hold leases
  on two runtimes.
- **Fencing**: owner IDs are deterministic so retries reuse a lease; each grant
  gets a monotonic generation, and a release quoting an older one is dropped
  rather than freeing the new holder.
- **Grant order**: reserve, write the row (grant-or-get), then announce. An
  ambiguous write is re-read and trusted only if every immutable field matches;
  otherwise capacity stays blocked rather than granting twice.
- **Releases** return `released`, `already_released`, `stale`, `conflict`, or
  `retryable`; only the first two free the slot.
- **Expiry** moves the row to `cleanup_requested` and keeps the slot spent until
  the owner is verified terminal, so a second credential consumer cannot start.
  Cleanup outliving its lease by `_LEASE_CLEANUP_ESCALATION_SECONDS` surfaces on
  `get_state` as `cleanup_unresolved` or `cleanup_owner_unverifiable`.
- Grants and releases touch one row, contradictory identities are recorded as
  evidence, and rows hold no credential material.

New Omnigent executions check their serialized Activity request against a 64 KiB
budget before taking any lease.

### 9.6 Replay compatibility

The manager replays its history on every restart and Continue-As-New, so changes
to grant timing are gated by Temporal patch markers
(`provider-profile-manager-*-v1` in
`moonmind/workflows/temporal/workflows/provider_profile_manager.py`), letting
each history keep its recorded commands. Rollover payloads carry unresolved
releases, requested cleanups, and uncommitted reservations, because a continued
run does not reload the ledger.

A marker is read once at startup, so a history that recorded new commands
without their marker cannot be distinguished from an older generation, and
another marker would only move the problem; such histories wedge and are
handled by 9.7. Deployment replaces the manager's worker directly rather than
keeping a second worker for old histories, because two code versions polling one
queue is itself a way to wedge it: newer code replays older histories through
`workflow.patched`, but not the reverse.

### 9.7 Wedged-singleton recovery

A worker that cannot replay a history leaves the singleton `RUNNING` in a
`WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR` loop; `get_state` and
Updates fail, blocking admission, OAuth, validation, and enrollment for that
runtime. Replay compatibility cannot be guaranteed by review alone, so this is
treated as recoverable.

`moonmind/provider_profiles/manager_recovery.py` alone replaces the run. The
lease client calls it when an Update fails at the RPC level; the
`provider_profile.manager_state` inspection calls it for signal-only traffic,
which a wedged run still accepts. Replacement requires confirmed nondeterminism
in the failed-task tail (`moonmind/workflows/skills/provider_manager_liveness.py`),
terminates only the observed run ID, requires the ledger (read directly) to hold
no rows outside `released` — a `NULL` or unknown state counts as possibly live —
and waits for the successor's `startup_restored`. The successor restores held
and cleanup rows; queued requesters re-queue via their 120-second slot-wait
timeout. An unreadable ledger refuses replacement, and the original error is
kept in the termination reason and logs.

A refusal returns HTTP 503 `provider_credential_manager_unavailable` with a
`refusal` (`held_lease_present`, `ledger_unreadable`, `no_replay_wedge`,
`manager_not_running`, `recovery_failed`, `manager_unreachable`), the
`retry_idempotency_key`, and a note that credentials are unchanged.
`held_lease_present` cannot clear by waiting, since the wedged run cannot process
the release. The operator then:

1. Reads the ledger (`SELECT lease_state, count(*) FROM
   provider_profile_slot_leases WHERE runtime_id = '<runtime>' GROUP BY
   lease_state`), verifies whether each unreleased row's consumer still runs,
   and reconciles or releases it.
2. Confirms the wedge with `temporal workflow describe` on
   `provider-profile-manager:<runtime>`.
3. Retries the operation. To drive Temporal directly, terminate the run with the
   cause in the reason and start `MoonMind.ProviderProfileManager` with
   `{"runtime_id":"<runtime>"}` on the queue from
   `moonmind.workflows.temporal.activity_catalog.get_workflow_task_queue()`,
   which honours `TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE`.
4. Verifies `get_state`, that fencing resumes at or above the old numbers, and
   that `current_leases` matches the ledger. Waiting AgentRuns need no re-signal.

Deployment updates do not wait for manager liveness, since that would block the
repair exactly when the manager is broken. See
[Docker Compose Deployment Update System](../Steps/DockerComposeUpdateSystem.md).

---

## 10. Runtime Materialization

The launcher layers the environment: base environment and runtime defaults;
load the profile and re-check readiness; resolve model/effort through the
canonical resolver; blank `clear_env_keys`; resolve `secret_refs` into ephemeral
values; materialize `file_templates`; apply `env_template` and
`home_path_overrides`; apply strategy shaping; build the command and launch.
Templates render resolved values, never refilling nulls from scalar defaults.
Materialization layers onto the base rather than replacing it, which would drop
`PATH`, `HOME`, and process context.

Runtime strategies interpret `command_behavior` and the resolved model: Claude
may suppress `--model` when model variables are set, Codex may select a
generated named config profile, and a proxy-first strategy may point provider
URLs at MoonMind proxy endpoints.

---

## 11. Security Properties

- Workflow payloads carry only `profile_id` and selectors, never keys, tokens,
  or secret-bearing config.
- Profile rows hold `SecretRef`s, templates, volume references, and redacted
  readiness metadata, never secret values.
- `SecretRef`s are resolved only at controlled execution boundaries, for the
  minimum time needed.
- Logs, artifacts, metadata, diagnostics, and audit rows redact secret-like
  strings, resolved environment values, credential-bearing config, and terminal
  output that may contain OAuth codes; generated config with secrets is not a
  durable artifact.
- OAuth volumes are dedicated, and one runtime does not read another's
  credential state unless that sharing is explicitly designed.
- Competing variables are cleared to prevent provider fallback.
- Proxy-first execution is preferred where MoonMind owns the outbound path.
- Only successful user-initiated setup auto-enables a profile.

---

## 12. Example: Codex CLI + MiniMax

A `composite` profile combines a provider config entry and named profile with an
environment variable holding the key. A Claude Code + MiniMax profile instead
uses an `env_bundle` that sets `ANTHROPIC_BASE_URL` to MiniMax's
Anthropic-compatible endpoint, `ANTHROPIC_AUTH_TOKEN` from the secret, the model
variables, and `suppress_cli_model_flag_when_env_model_present`.

```yaml
profile_id: codex_minimax_m27
runtime_id: codex_cli
provider_id: minimax
credential_source: secret_ref
runtime_materialization_mode: composite
enabled: true
auth_state: connected
priority: 120
model_tiers:
  - { label: MiniMax default, model: "codex-MiniMax-M2.7", effort: null }
default_model_tier: 1
max_parallel_runs: 4
cooldown_after_429_seconds: 600
secret_refs:
  provider_api_key: { secret_id: sec_minimax_m27, backend_type: db_encrypted }
clear_env_keys: [OPENAI_API_KEY]
env_template:
  MINIMAX_API_KEY: { from_secret_ref: provider_api_key }
file_templates:
  - path: "~/.codex/config.toml"
    format: toml
    merge_strategy: deep_merge
    permissions: "0600"
    content_template:
      model_providers:
        minimax:
          base_url: "https://api.minimax.io/v1"
          env_key: "MINIMAX_API_KEY"
          wire_api: "chat"
          requires_openai_auth: false
      profiles:
        m27: { model: "{{resolved_model}}", model_provider: "minimax" }
command_behavior:
  default_codex_profile_name: "m27"
```
